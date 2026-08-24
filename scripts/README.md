# Scripts

## `bootstrap.sh` — dev/test environment

Creates a local `./.venv` (gitignored) with Home Assistant, the test harness,
ruff, and the `aiosolaredge-one` client. Reproducible from a fresh clone on any
machine — see the script header for `PYTHON` / `VENV` / `SOLAREDGE_LIB_PATH`
overrides and [`../CLAUDE.md`](../CLAUDE.md) for the full dev workflow.

```bash
scripts/bootstrap.sh
.venv/bin/python -m pytest tests -q
```

---

# Phase 0 capture spike

Turns the remaining `TBD`s in [`../prd/02-api-and-rate-limiting.md`](../prd/02-api-and-rate-limiting.md)
into facts — cheaply and safely. See that PRD for what we're trying to learn
(full endpoint list, the credit/quota model, rate-limit headers).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r scripts/requirements.txt
```

## Credentials (never commit these)

v2 uses **two headers**, not a bearer token:

```bash
export SOLAREDGE_ACCOUNT_KEY=...   # X-Account-Key (fleet/account)
export SOLAREDGE_API_KEY=...       # X-API-Key (user)
# Site Owner plans may only have one — set whatever you have.
```

## Run it

```bash
# 1. See exactly what it would call, spending ZERO credits:
python scripts/phase0_capture.py --dry-run

# 2. Core endpoints only (/sites, /overview, /devices) — ~3 calls:
python scripts/phase0_capture.py

# 3. Also probe candidate endpoints (energy/power/battery/alerts/...):
python scripts/phase0_capture.py --probe        # respects --max-calls (default 8)

# Useful flags:
#   --site-id 12345   skip the /sites listing
#   --keep-raw        also save UNREDACTED bodies to fixtures/_raw (gitignored)
#   --max-calls N     raise/lower the budget guard
```

## Outputs

- `fixtures/<name>.json` — **redacted** response bodies (safe to commit; used as
  library test fixtures).
- `fixtures/call_log.jsonl` — one line per request with status, timing, and the
  **full response headers** (this is how we discover the credit/rate-limit model).
- `fixtures/_raw/` — only with `--keep-raw`; unredacted, **gitignored**.

## After running

1. Open `fixtures/call_log.jsonl` and look for headers hinting at credits/limits
   (the script also prints any it spots): `X-RateLimit-*`, `X-Credits-*`,
   `Retry-After`, `*-Remaining`, `*-Quota`, etc.
2. Note which probed endpoints returned `200` vs `404`/`400` — that's the real
   v2 endpoint list.
3. Fill in `../prd/02-api-and-rate-limiting.md` (endpoint table + credit model),
   then we move to Phase 1 (the `aiosolaredge-one` client) against these fixtures.
