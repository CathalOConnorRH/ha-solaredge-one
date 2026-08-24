#!/usr/bin/env python3
"""Phase 0 capture spike for the SolarEdge ONE (v2) API.

Goal: turn the remaining `TBD`s in prd/02-api-and-rate-limiting.md into facts,
cheaply and safely. It:

  1. Calls a small, fixed set of v2 endpoints (optionally probes candidates).
  2. Logs EVERY request + all response headers to a JSONL file so we can
     discover the credit/quota/rate-limit model empirically (look for headers
     like X-RateLimit-Remaining, X-Credits-*, Retry-After, etc.).
  3. Writes REDACTED JSON fixtures (secrets + PII stripped) for use as test
     fixtures in the future `aiosolaredge-one` library.

Budget safety:
  - Hard cap on total API calls (--max-calls, default 8). The script refuses to
    exceed it and stops early.
  - Nothing runs without explicit credentials in the environment.
  - Use --dry-run to see exactly what it WOULD call without spending a credit.

Auth (v2 uses headers, not a bearer token):
  export SOLAREDGE_ACCOUNT_KEY=...   # X-Account-Key (fleet/account)
  export SOLAREDGE_API_KEY=...       # X-API-Key (user)

For a Site Owner plan you may only have one of these — set whatever you have;
the script sends only the headers that are present.

Usage:
  python scripts/phase0_capture.py --dry-run
  python scripts/phase0_capture.py                       # core endpoints only
  python scripts/phase0_capture.py --probe               # + probe candidates
  python scripts/phase0_capture.py --site-id 12345       # skip /sites listing
  python scripts/phase0_capture.py --max-calls 12 --keep-raw
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - guidance only
    sys.exit("Missing dependency: pip install requests  (see scripts/requirements.txt)")

BASE_URL = os.environ.get("SOLAREDGE_BASE_URL", "https://monitoringapi.solaredge.com/v2")

# --- Fixtures we always try (cheap, known to exist) -------------------------
# name -> path template ({site} substituted). Order matters: /sites first so we
# can auto-discover a site id for the rest.
CORE_ENDPOINTS: list[tuple[str, str]] = [
    ("sites", "/sites"),
    ("overview", "/sites/{site}/overview"),
    ("devices", "/sites/{site}/devices"),
]

# --- Candidate paths to PROBE (existence unknown; opt-in via --probe) --------
# Many will 404 or 400-without-params; either outcome tells us whether the
# endpoint exists in v2. Each still counts against --max-calls.
PROBE_ENDPOINTS: list[tuple[str, str]] = [
    ("energy", "/sites/{site}/energy"),
    ("power", "/sites/{site}/power"),
    ("power_flow", "/sites/{site}/powerFlow"),
    ("storage", "/sites/{site}/storage"),
    ("battery", "/sites/{site}/battery"),
    ("alerts", "/sites/{site}/alerts"),
    ("environmental", "/sites/{site}/environmentalBenefits"),
    ("summary", "/sites/{site}/summary"),
]

# --- Redaction --------------------------------------------------------------
# Values of keys whose lowercased name contains any of these substrings are
# replaced with a placeholder. Structure/types are preserved for fixture use.
SENSITIVE_KEY_SUBSTRINGS = (
    "serial", "sn", "name", "address", "city", "country", "zip", "postal",
    "location", "lat", "lon", "lng", "longitude", "latitude", "gps",
    "email", "phone", "account", "owner", "installer", "contact", "notes",
    "apikey", "api_key", "token", "secret",
)
# Header names (lowercased) worth surfacing in the summary as credit/rate signals.
INTERESTING_HEADER_HINTS = (
    "rate", "limit", "credit", "quota", "remaining", "retry", "reset", "usage",
)


class BudgetExceeded(Exception):
    """Raised when the configured max-calls cap would be exceeded."""


class Capturer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.calls_made = 0
        self.outdir = Path(args.outdir)
        self.raw_dir = self.outdir / "_raw"
        self.log_path = self.outdir / "call_log.jsonl"
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        acct = os.environ.get("SOLAREDGE_ACCOUNT_KEY")
        api = os.environ.get("SOLAREDGE_API_KEY")
        if acct:
            self.session.headers["X-Account-Key"] = acct
        if api:
            self.session.headers["X-API-Key"] = api
        self._have_creds = bool(acct or api)
        self._interesting_headers: set[str] = set()

    # -- helpers -------------------------------------------------------------
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _log(self, record: dict[str, Any]) -> None:
        self.outdir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                kl = str(k).lower()
                if any(sub in kl for sub in SENSITIVE_KEY_SUBSTRINGS):
                    out[k] = self._placeholder(v)
                else:
                    out[k] = self._redact(v)
            return out
        if isinstance(value, list):
            return [self._redact(v) for v in value]
        return value

    @staticmethod
    def _placeholder(v: Any) -> Any:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return 0
        if v is None:
            return None
        if isinstance(v, (dict, list)):
            return "REDACTED"
        return "REDACTED"

    def _call(self, name: str, path: str, *, params: dict | None = None) -> dict | None:
        url = BASE_URL + path
        if self.args.dry_run:
            print(f"  [dry-run] GET {path}")
            return None
        if self.calls_made >= self.args.max_calls:
            raise BudgetExceeded(
                f"Reached --max-calls={self.args.max_calls}; stopping to protect budget."
            )
        if self.calls_made and self.args.delay:
            time.sleep(self.args.delay)  # respect per-minute rate limit
        self.calls_made += 1
        started = time.monotonic()
        record: dict[str, Any] = {
            "ts": self._now(),
            "name": name,
            "method": "GET",
            "path": path,
            "params": params or {},
        }
        try:
            resp = self.session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            record["error"] = repr(exc)
            self._log(record)
            print(f"  GET {path} -> ERROR {exc}")
            return record
        elapsed_ms = round((time.monotonic() - started) * 1000)
        headers = {k: v for k, v in resp.headers.items()}
        record.update(
            {
                "status": resp.status_code,
                "elapsed_ms": elapsed_ms,
                "response_headers": headers,
                "body_bytes": len(resp.content),
            }
        )
        # Track any header that looks credit/rate related.
        for hk in headers:
            if any(hint in hk.lower() for hint in INTERESTING_HEADER_HINTS):
                self._interesting_headers.add(hk)
        # Parse + persist body.
        body: Any = None
        try:
            body = resp.json()
        except ValueError:
            record["body_text_snippet"] = resp.text[:500]
        self._log(record)

        status_note = "OK" if resp.ok else "!!"
        print(f"  GET {path} -> {resp.status_code} {status_note} "
              f"({elapsed_ms} ms, {len(resp.content)} B)")

        if body is not None:
            if self.args.keep_raw:
                self.raw_dir.mkdir(parents=True, exist_ok=True)
                (self.raw_dir / f"{name}.json").write_text(
                    json.dumps(body, indent=2), encoding="utf-8"
                )
            redacted = self._redact(copy.deepcopy(body))
            (self.outdir / f"{name}.json").write_text(
                json.dumps(redacted, indent=2), encoding="utf-8"
            )
        record["_body"] = body  # for in-run use only (not logged again)
        return record

    # -- flow ----------------------------------------------------------------
    def _discover_site_id(self, sites_record: dict | None) -> str | None:
        if self.args.site_id:
            return self.args.site_id
        if not sites_record:
            return None
        body = sites_record.get("_body")
        # Be liberal about shape: {"sites":[{"siteId":..}]} or [{...}] etc.
        candidates: list[Any] = []
        if isinstance(body, dict):
            for key in ("sites", "data", "items", "results"):
                val = body.get(key)
                if isinstance(val, list):
                    candidates = val
                    break
                # nested, e.g. {"sites": {"count": N, "site": [...]}}
                if isinstance(val, dict):
                    for k2 in ("site", "sites", "items", "list", "results"):
                        if isinstance(val.get(k2), list):
                            candidates = val[k2]
                            break
                    if candidates:
                        break
        elif isinstance(body, list):
            candidates = body
        for item in candidates:
            if isinstance(item, dict):
                for key in ("siteId", "site_id", "id"):
                    if key in item:
                        return str(item[key])
        return None

    def run(self) -> int:
        print(f"SolarEdge ONE v2 capture spike  (base={BASE_URL})")
        if not self._have_creds and not self.args.dry_run:
            print("ERROR: set SOLAREDGE_ACCOUNT_KEY and/or SOLAREDGE_API_KEY.",
                  file=sys.stderr)
            return 2
        hdrs = [h for h in ("X-Account-Key", "X-API-Key")
                if h in self.session.headers]
        print(f"Auth headers present: {hdrs or '(none - dry run)'}")
        print(f"Max calls: {self.args.max_calls} | probe: {self.args.probe} | "
              f"outdir: {self.outdir}")
        print("-" * 60)

        try:
            # 1) /sites (unless a site id was supplied and we don't need listing)
            sites_record = None
            if not self.args.site_id:
                print("Core endpoints:")
                sites_record = self._call("sites", "/sites")
            site_id = self._discover_site_id(sites_record)

            if not site_id and not self.args.dry_run:
                print("\nNo site id discovered; cannot call site-scoped endpoints.")
                print("Pass --site-id <id> to continue.")
                self._summary()
                return 0
            site_id = site_id or "SITE_ID"
            if self.args.site_id:
                print("Core endpoints:")

            # 2) remaining core, site-scoped (skipped in --only-probe mode)
            if not self.args.only_probe:
                for name, tmpl in CORE_ENDPOINTS:
                    if name == "sites":
                        continue
                    self._call(name, tmpl.format(site=site_id))

            # 3) optional probes
            if self.args.probe or self.args.only_probe:
                print("\nProbing candidate endpoints (existence unknown):")
                for name, tmpl in PROBE_ENDPOINTS:
                    self._call(name, tmpl.format(site=site_id))

        except BudgetExceeded as exc:
            print(f"\n{exc}")
        finally:
            self._summary()
        return 0

    def _summary(self) -> None:
        print("-" * 60)
        print(f"Calls made: {self.calls_made}")
        if self._interesting_headers:
            print("Credit/rate-limit-looking response headers observed:")
            for h in sorted(self._interesting_headers):
                print(f"  - {h}")
            print("  -> Record these + their values from call_log.jsonl into "
                  "prd/02-api-and-rate-limiting.md (credit model).")
        elif not self.args.dry_run and self.calls_made:
            print("No obvious credit/rate-limit headers seen. Inspect "
                  f"{self.log_path} for the full header dumps.")
        if not self.args.dry_run and self.calls_made:
            print(f"Redacted fixtures written to: {self.outdir}/")
            print(f"Full call log: {self.log_path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SolarEdge ONE v2 Phase 0 capture spike")
    p.add_argument("--outdir", default="fixtures",
                   help="where redacted fixtures + call log are written (default: fixtures)")
    p.add_argument("--max-calls", type=int, default=8,
                   help="hard cap on API calls (budget guard, default: 8)")
    p.add_argument("--probe", action="store_true",
                   help="also probe candidate endpoints (energy/power/battery/alerts/...)")
    p.add_argument("--only-probe", action="store_true",
                   help="probe candidates only; skip /sites, /overview, /devices (needs --site-id)")
    p.add_argument("--delay", type=float, default=0.0,
                   help="seconds to sleep between calls (use ~6 to respect a 10/min limit)")
    p.add_argument("--site-id", default=None,
                   help="use this site id and skip the /sites listing call")
    p.add_argument("--keep-raw", action="store_true",
                   help="also write UNREDACTED bodies to fixtures/_raw (gitignored)")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be called; make no requests")
    return p.parse_args(argv)


def main() -> int:
    args = parse_args()
    return Capturer(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
