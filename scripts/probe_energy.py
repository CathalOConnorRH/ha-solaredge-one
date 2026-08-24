#!/usr/bin/env python3
"""Probe the SolarEdge ONE (v2) ``/energy`` endpoint to unlock lifetime /
this-month / this-year energy sensors.

Background: ``/sites/{id}/overview`` only returns *today's* running totals (not
lifetime — verified against a live site). ``/energy`` returns a time series over
a requested ``from``/``to`` window at a given ``resolution``. If the API accepts
a coarse resolution (DAY/MONTH/YEAR) over a wide window, we can sum it to derive
lifetime / month-to-date / year-to-date and add proper sensors.

This script figures out empirically:
  1. Which ``resolution`` values the API accepts (and whether the query param is
     called ``resolution`` at all — it also tries ``timeUnit`` / ``unit``).
  2. What a lifetime-range query returns, and whether its sum matches the figure
     shown in the SolarEdge portal.
  3. Month-to-date and year-to-date sums.

Budget safety (same posture as phase0_capture.py):
  * Hard ``--max-calls`` cap (default 18). Each call is ~1 credit.
  * ``--dry-run`` prints exactly what it WOULD call, spending nothing.
  * Nothing runs without credentials in the environment.
  * ``--delay`` (default 6s) spaces calls under a 10/min limit.

Auth (v2 uses headers, not a bearer token):
  export SOLAREDGE_API_KEY=...       # X-API-Key (required)
  export SOLAREDGE_ACCOUNT_KEY=...   # X-Account-Key (Fleet accounts only)

Usage:
  python scripts/probe_energy.py --dry-run
  python scripts/probe_energy.py                       # auto-discovers site id
  python scripts/probe_energy.py --site-id 3066774
  python scripts/probe_energy.py --install-date 2022-08-15 --max-calls 24
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - guidance only
    sys.exit("Missing dependency: pip install requests  (see scripts/requirements.txt)")

BASE_URL = os.environ.get("SOLAREDGE_BASE_URL", "https://monitoringapi.solaredge.com/v2")

# Resolutions to try over a short recent window. Order coarse-last so the summary
# reads naturally; we test them all to see which the API echoes/accepts.
RESOLUTIONS = ["QUARTER_HOUR", "HOUR", "DAY", "WEEK", "MONTH", "YEAR"]

# Fallback query-param names for the resolution, tried only if `resolution` is
# rejected outright.
RESOLUTION_PARAM_CANDIDATES = ["resolution", "timeUnit", "unit", "aggregation"]


def _iso(dt: datetime) -> str:
    """ISO-8601 in UTC with a trailing Z (the format the v2 captures used)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Prober:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.calls_made = 0
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        api = os.environ.get("SOLAREDGE_API_KEY")
        acct = os.environ.get("SOLAREDGE_ACCOUNT_KEY")
        if api:
            self.session.headers["X-API-Key"] = api
        if acct:
            self.session.headers["X-Account-Key"] = acct
        self._have_creds = bool(api)
        self.findings: list[dict[str, Any]] = []

    def _call(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Return {status, ok, body, error} for a GET; respects budget + delay."""
        clean = {k: v for k, v in params.items() if v is not None}
        if self.args.dry_run:
            print(f"  [dry-run] GET {path}  {clean}")
            return {"status": None, "ok": None, "body": None, "error": None}
        if self.calls_made >= self.args.max_calls:
            sys.exit(f"Reached --max-calls={self.args.max_calls}; stopping to protect budget.")
        if self.calls_made and self.args.delay:
            time.sleep(self.args.delay)
        self.calls_made += 1
        try:
            resp = self.session.get(BASE_URL + path, params=clean, timeout=30)
        except requests.RequestException as exc:
            print(f"  GET {path} {clean} -> ERROR {exc}")
            return {"status": None, "ok": False, "body": None, "error": repr(exc)}
        body: Any = None
        try:
            body = resp.json()
        except ValueError:
            body = None
        note = "OK" if resp.ok else "!!"
        snippet = "" if resp.ok else f"  {(resp.text or '')[:160]!r}"
        print(f"  GET {path} {clean} -> {resp.status_code} {note}{snippet}")
        return {"status": resp.status_code, "ok": resp.ok, "body": body, "error": None}

    # -- series helpers ------------------------------------------------------
    @staticmethod
    def _summarize_series(body: Any) -> dict[str, Any]:
        """Pull unit/resolution/#values/sum out of a TimeSeries-shaped body."""
        if not isinstance(body, dict):
            return {"parsed": False}
        values = body.get("values") or []
        nums = [
            v.get("value")
            for v in values
            if isinstance(v, dict) and isinstance(v.get("value"), (int, float))
        ]
        return {
            "parsed": True,
            "unit": body.get("unit"),
            "resolution_echoed": body.get("resolution"),
            "n_values": len(values),
            "n_non_null": len(nums),
            "sum": round(sum(nums), 1) if nums else 0,
            "first_ts": (values[0].get("timestamp") if values else None),
            "last_ts": (values[-1].get("timestamp") if values else None),
        }

    # -- phases --------------------------------------------------------------
    def resolve_site(self) -> tuple[str | None, str | None]:
        """Return (site_id, install_date) — from args, else GET /sites."""
        site_id = self.args.site_id
        install = self.args.install_date
        if site_id and install:
            return site_id, install
        if self.args.dry_run and not site_id:
            print("  [dry-run] would GET /sites to discover a site id")
            return site_id or "<SITE_ID>", install
        res = self._call("/sites", {})
        body = res.get("body")
        container = (body or {}).get("sites", body) if isinstance(body, dict) else {}
        items = container.get("site") if isinstance(container, dict) else None
        if items:
            first = items[0]
            site_id = site_id or str(first.get("siteId"))
            install = install or first.get("installationDate")
            print(f"  -> site_id={site_id}  installationDate={install}")
        return site_id, install

    def discover_resolution_param(self, site_id: str, window_from: datetime,
                                  window_to: datetime) -> str | None:
        """Find which query-param name carries the resolution (try DAY)."""
        for pname in RESOLUTION_PARAM_CANDIDATES:
            res = self._call(
                f"/sites/{site_id}/energy",
                {"from": _iso(window_from), "to": _iso(window_to), pname: "DAY"},
            )
            if res["ok"]:
                summ = self._summarize_series(res["body"])
                # If the echoed resolution is DAY, this param name "took".
                if summ.get("resolution_echoed") in (None, "DAY") and summ["parsed"]:
                    print(f"  -> resolution param appears to be '{pname}'")
                    return pname
        return None

    def run(self) -> None:
        if not self._have_creds and not self.args.dry_run:
            sys.exit("No SOLAREDGE_API_KEY in environment. Set it (and X-Account-Key for Fleet).")

        now = datetime.now(timezone.utc)
        recent_from = now - timedelta(days=3)

        print("== Resolve site ==")
        site_id, install = self.resolve_site()
        if not site_id:
            sys.exit("Could not determine a site id; pass --site-id.")

        # Installation date -> lifetime window start. Fall back to a safe early date.
        try:
            install_dt = datetime.strptime((install or "")[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, TypeError):
            install_dt = datetime(2000, 1, 1, tzinfo=timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

        print("\n== Phase A: which resolutions does /energy accept? (recent 3-day window) ==")
        rparam = "resolution"
        accepted: list[str] = []
        for r in RESOLUTIONS:
            res = self._call(
                f"/sites/{site_id}/energy",
                {"from": _iso(recent_from), "to": _iso(now), rparam: r},
            )
            if res["status"] is None and not self.args.dry_run:
                continue
            summ = self._summarize_series(res.get("body")) if res["ok"] else {}
            self.findings.append({"phase": "A", "resolution": r, "status": res["status"],
                                  "ok": res["ok"], **summ})
            if res["ok"]:
                accepted.append(r)

        # If nothing worked, the param name may differ — probe it, then retry once.
        if not accepted and not self.args.dry_run:
            print("\n== Phase A': `resolution` rejected — probing alternate param names ==")
            found = self.discover_resolution_param(site_id, recent_from, now)
            if found and found != "resolution":
                rparam = found
                for r in ("DAY", "MONTH", "YEAR"):
                    res = self._call(
                        f"/sites/{site_id}/energy",
                        {"from": _iso(recent_from), "to": _iso(now), rparam: r},
                    )
                    summ = self._summarize_series(res.get("body")) if res["ok"] else {}
                    self.findings.append({"phase": "A", "resolution": r, "status": res["status"],
                                          "ok": res["ok"], "param": rparam, **summ})
                    if res["ok"]:
                        accepted.append(r)

        # Pick the coarsest accepted resolution for wide-window sums.
        coarse = next((r for r in ("YEAR", "MONTH", "WEEK", "DAY") if r in accepted), None)

        if coarse:
            print(f"\n== Phase B: wide-window sums using resolution={coarse} (param={rparam}) ==")
            for label, start in (
                ("lifetime", install_dt),
                ("year_to_date", year_start),
                ("month_to_date", month_start),
            ):
                # month-to-date is more useful at DAY granularity if available.
                use_res = "DAY" if (label == "month_to_date" and "DAY" in accepted) else coarse
                res = self._call(
                    f"/sites/{site_id}/energy",
                    {"from": _iso(start), "to": _iso(now), rparam: use_res},
                )
                summ = self._summarize_series(res.get("body")) if res["ok"] else {}
                self.findings.append({"phase": "B", "window": label, "resolution": use_res,
                                      "status": res["status"], "ok": res["ok"], **summ})
        else:
            print("\n== Phase B skipped: no coarse resolution accepted ==")

        self._report()

    def _report(self) -> None:
        if self.args.dry_run:
            print("\n(dry-run: no findings)")
            return
        print("\n" + "=" * 70)
        print("SUMMARY — paste this back to continue")
        print("=" * 70)
        print(f"calls spent: {self.calls_made}")
        print("\nPhase A (accepted resolutions over a recent window):")
        for f in self.findings:
            if f["phase"] != "A":
                continue
            if f.get("ok"):
                print(f"  {f['resolution']:<12} OK  echoed={f.get('resolution_echoed')} "
                      f"unit={f.get('unit')} n={f.get('n_values')} "
                      f"non_null={f.get('n_non_null')} sum={f.get('sum')}")
            else:
                print(f"  {f['resolution']:<12} status={f.get('status')}")
        print("\nPhase B (wide-window sums):")
        for f in self.findings:
            if f["phase"] != "B":
                continue
            if f.get("ok"):
                unit = f.get("unit")
                s = f.get("sum") or 0
                mwh = f"  (~{round(s/1_000_000, 2)} MWh)" if unit == "WH" else ""
                print(f"  {f['window']:<14} res={f['resolution']:<6} unit={unit} "
                      f"n={f.get('n_values')} sum={s}{mwh}  "
                      f"[{f.get('first_ts')} .. {f.get('last_ts')}]")
            else:
                print(f"  {f['window']:<14} status={f.get('status')}")
        if self.args.out:
            with open(self.args.out, "w", encoding="utf-8") as fh:
                json.dump(self.findings, fh, indent=2)
            print(f"\nfull findings written to {self.args.out}")
        print("\nNOTE: compare the 'lifetime' sum above to your SolarEdge portal "
              "figure. If it matches, we can add lifetime/this-month/this-year sensors.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--site-id", help="Skip /sites lookup and use this site id.")
    p.add_argument("--install-date", help="Installation date YYYY-MM-DD (lifetime window start).")
    p.add_argument("--max-calls", type=int, default=18, help="Hard cap on API calls (default 18).")
    p.add_argument("--delay", type=float, default=6.0,
                   help="Seconds between calls to respect the per-minute limit (default 6).")
    p.add_argument("--dry-run", action="store_true", help="Print planned calls; spend nothing.")
    p.add_argument("--out", help="Write full findings JSON to this path.")
    Prober(p.parse_args()).run()


if __name__ == "__main__":
    main()
