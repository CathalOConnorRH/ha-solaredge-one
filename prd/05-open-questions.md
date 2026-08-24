# 05 — Open Questions & Assumptions

Resolve these as we go. Items marked **(blocks Phase 0/1)** gate the API client.

## Resolved in Phase 0 (2026-08-24) ✅
- **Credit model:** 1 call = 1 unit (per-minute header decrements by 1/call).
- **Auth transport:** headers `X-API-Key` (required) + optional `X-Account-Key`;
  single API key authenticated fine. Base URL `https://monitoringapi.solaredge.com/v2`.
- **Rate-limit signalling:** per-minute exposed (`x-ratelimit-limit-minute` /
  `-remaining-minute`); monthly quota NOT exposed → track locally.
- **Live power endpoint:** `GET /sites/{id}/power` (15-min avg time series; use
  last non-null value as "current"). 1 credit/call.
- **Site listing:** `GET /sites` returns `{sites:{count,site:[...]}}`.

## Blocking — small residual (find in docs)
- **Rate-limit signalling:** does a **monthly-quota** endpoint/header exist
  anywhere? (Determines accurate "credits remaining" sensor vs. local estimate.)
- **Storage/battery + environmental-benefits** real v2 paths (guessed paths 404).
- **Query params** for `/energy` `/power` `/alerts` (from/to/resolution/timeUnit)
  and any range caps.
- **Optimizer/panel telemetry:** endpoint + cost (likely `/services/layout/...`).
- **429 behaviour:** body shape + `Retry-After` (not yet observed).
- **Fleet token differences:** does it add `X-Account-Key` and multi-site results?

## Product decisions
- **Integration domain name:** `solaredge_one` ✅ confirmed.
- **Library name:** `aiosolaredge-one` ✅ confirmed.
- **Control/write features** (e.g. set backup reserve): ✅ out of scope for v1
  (read-only).
- **Min HA version:** assumed current stable (~2026.8+); pick a concrete floor.
- **EV charger / non-PV ONE products:** out of scope for v1 — confirm.
- **Default safety factor 0.7 and night slowdown ×3:** acceptable defaults?
- **Per-optimizer entities default off:** acceptable (credit + registry cost)?

## Risks
- SolarEdge ONE is new; endpoints/quotas may change — isolate all API knowledge
  in the library to limit blast radius.
- If credits are consumed faster than expected, device-level/optimizer tiers may
  need to default off for Fleet users with many sites.
- Reference account limits (2000/mo, 10/min) may not generalize — everything is
  driven by user-supplied config, not constants.

## Parking lot / later
- HA statistics backfill from historical energy endpoints on first setup.
- Multiple tokens in one HA instance (mixed Fleet + Site Owner).
- Localizations beyond English.
