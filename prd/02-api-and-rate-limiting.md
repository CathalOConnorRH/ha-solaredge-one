# 02 — API Surface & Rate Limiting

> ⚠️ The SolarEdge ONE docs are client-rendered SPAs (main portal + Stoplight)
> and can't be scraped automatically. Confirmed facts below come from the
> official demo repo + community integrations; remaining `TBD`s are filled from
> the docs / logged live calls during **Phase 0**.

## Confirmed facts (2026-08-24)

- **Base URL:** `https://monitoringapi.solaredge.com/v2`
- **Auth:** header-based (NOT bearer):
  - `X-Account-Key` — identifies the SolarEdge **account** (fleet level)
  - `X-API-Key` — identifies the specific SolarEdge **user**
  - Some endpoints accept either method; some require the API Key. OAuth
    ("SolarEdge Connect") was announced as a future auth method.
  - **Plan-type mapping (to verify in Phase 0):** Fleet ("My Fleet") likely
    supplies both keys and can list many sites; Site Owner likely uses the
    API Key path and is single-site. Confirm exactly which header(s) each plan
    provides and which endpoints each can reach.
- **Data hierarchy (3 levels):** Fleet → Site → Device.
- **Known endpoints (confirmed):**
  - `GET /sites` — site list (fleet level)
  - `GET /sites/{siteId}/overview` — site overview
  - `GET /sites/{siteId}/devices` — site inventory (devices)
  - `GET /sites/{siteId}/inverters/{sn}/voltage` — inverter voltage (device level)
- **Official reference docs (Stoplight, SPA):**
  `https://se-api.stoplight.io/docs/monitoring/` — e.g. `833cd5efe90d0-site-list`,
  `bc101de319142-site-overview`, `30abc1f8b9210-site-inventory`,
  `30f0aa1d645cc-inverter-voltage`.
- **Prior art / references:**
  - `talbronfer/monitoring-v2-api-demo` — official-style v2 quickstart.
  - `AndrewTapp/solaredgeoptimizers` — ONE-aware HACS integration; uses
    `/services/layout/...` for ONE with legacy fallback (good for optimizer/panel
    telemetry patterns).
## Phase 0 LIVE findings (2026-08-24, token = single API key, 1 site)

Captured with `scripts/phase0_capture.py` (fixtures in `fixtures/`). This token
authenticated with **`X-API-Key` only** — no `X-Account-Key` needed (Site
Owner-style single-key auth). Site id `3066774`, 1 inverter + 14 optimizers, no
battery, no consumption meter.

Rate limit / credit model
- Response headers expose **per-minute** limit only:
  `x-ratelimit-limit-minute: 10`, `x-ratelimit-remaining-minute: N`.
- Each successful call decremented `remaining-minute` by exactly **1** →
  **1 call = 1 unit**. Treat monthly credits the same (1 credit/call) until
  proven otherwise.
- The **monthly 2000-credit quota is NOT in any header** → must be tracked
  locally in the persisted ledger (see 01/03). No `*-month`/`*-credit` header.
- 429 body/shape + `Retry-After` **not yet observed** (never hit the limit).
- Infra: Cloudflare + Kong gateway (`via: kong/...`, `x-kong-*` headers).

Endpoint existence (GET, no params, at guessed paths)
- ✅ `/sites` · ✅ `/sites/{id}/overview` · ✅ `/sites/{id}/devices`
  · ✅ `/sites/{id}/energy` · ✅ `/sites/{id}/power` · ✅ `/sites/{id}/alerts`
- ❌ 404: `/powerFlow`, `/storage`, `/battery`, `/environmentalBenefits`,
  `/summary` — real v2 paths unknown; find in docs. (Battery/consumption data
  for metered/storage sites likely surfaces via `/overview` fields, which are
  `null` on this un-metered site.)

Response shapes (see `fixtures/*.json`)
- `/sites` → `{"sites": {"count": N, "site": [ {siteId, name, peakPower,
  installationDate, location, activationStatus, note} ]}}` (note singular `site`).
- `/overview` → `{siteId, production:{total, unit:"WH", toSelfConsumption,
  toStorage, toGrid}, consumption:{total, unit:"WH", fromPv, fromStorage,
  fromGrid}}` — **cumulative lifetime totals** (best source for HA
  `total_increasing` energy sensors). Breakdown fields are `null` without meters.
- `/devices` → `[ {type:"INVERTER", serialNumber, manufacturer, partNumber,
  createdAt, firmwareVersion, active, name, communicationType, firmware,
  connectedOptimizers} ]`.
- `/energy` & `/power` → `{period:{from,to}, unit:"WH"|"W",
  resolution:"QUARTER_HOUR", values:[{timestamp, value|null}]}`. Default range =
  today. `/power` value is a 15-min average; **current power = last non-null
  value**. Good for Energy Dashboard statistics backfill.
- `/alerts` → JSON array (`[]` when none).

### Still to resolve from docs (small residual)
- Real v2 paths for **storage/battery** and **environmental benefits**.
- Accepted **query params** for `/energy`, `/power`, `/alerts` (from/to,
  resolution, timeUnit) and any range caps.
- Device-level telemetry endpoints beyond `/inverters/{sn}/voltage`
  (power/temperature) and optimizer/panel data (`/services/layout/...`).
- 429 behaviour + whether a monthly-quota endpoint/header exists anywhere.
- Whether a Fleet token adds `X-Account-Key` and multi-site `/sites` results.

## Phase 0 — API capture checklist

Authentication
- [x] Base URL: `https://monitoringapi.solaredge.com/v2` (same host both plans — confirm)
- [x] API version segment: `/v2`
- [x] Token transport: headers `X-Account-Key` + `X-API-Key` (not bearer)
- [ ] Difference in auth between Fleet and Site Owner tokens (which header each provides, which endpoints each can reach): `TBD`
- [ ] Token validation endpoint for config flow — `GET /sites` is the likely cheapest verifier: confirm cost `TBD`
- [ ] Reauth trigger — which status/error signals an expired/invalid token: `TBD`

Quota / rate limits
- [x] Credit model — **1 call = 1 unit** (each call decrements remaining-minute by 1)
- [x] Monthly credit quota field — **not exposed in headers**; track locally
- [x] Per-minute call limit — **10** via `x-ratelimit-limit-minute` (confirm Fleet plan)
- [x] Rate-limit response headers — `x-ratelimit-limit-minute`, `x-ratelimit-remaining-minute`
- [ ] 429 body/shape + `Retry-After` — not yet observed: `TBD`
- [ ] Whether some endpoints are free / cheaper than others (all seen = 1): assume no

Endpoints (fill in path, params, credit cost, response shape → save a fixture)
- [ ] **List sites** (Fleet): `TBD`
- [ ] **Site details / metadata**: `TBD`
- [ ] **Energy** (production/consumption/import/export, time series + totals): `TBD`
- [ ] **Power** (live/near-real-time power flow): `TBD`
- [ ] **Battery/storage** (SoC, charge/discharge power, reserve): `TBD`
- [ ] **Inverters** (list + telemetry): `TBD`
- [ ] **Optimizers / panels** (list + per-module telemetry): `TBD`
- [ ] **Alerts / status** (site + device health, faults): `TBD`
- [ ] **Environmental benefits** (CO₂, etc.): `TBD`
- [ ] Supported `timeUnit` / resolution and time-range caps per endpoint: `TBD`

Deliverable of Phase 0: a filled table below + JSON fixtures in the library repo
under `tests/fixtures/` (one per endpoint, secrets redacted).

### Endpoint reference table (fill during Phase 0)

| Area | Method + Path | Key params | Credit cost | Tier (fast/slow/daily) | Fixture file |
|------|---------------|-----------|-------------|------------------------|--------------|
| List sites | `GET /sites` ✅ | — | 1/call | daily | `sites.json` ✅ |
| Site overview | `GET /sites/{siteId}/overview` ✅ | — | 1/call | fast | `overview.json` ✅ |
| Site inventory | `GET /sites/{siteId}/devices` ✅ | — | 1/call | daily | `devices.json` ✅ |
| Energy | `GET /sites/{siteId}/energy` ✅ | from, to, resolution (default today/QUARTER_HOUR) | 1/call | slow | `energy.json` ✅ |
| Power | `GET /sites/{siteId}/power` ✅ | from, to, resolution | 1/call | fast | `power.json` ✅ |
| Alerts | `GET /sites/{siteId}/alerts` ✅ | `TBD` (params) | 1/call | slow | `alerts.json` ✅ (empty) |
| Inverter telemetry | `GET /sites/{siteId}/inverters/{sn}/voltage` (+ power/temp?) | date range, resolution | 1/call | slow | `TBD` |
| Battery/storage | ❌ not at `/storage` or `/battery` — path `TBD` | — | 1/call | fast | `TBD` |
| Optimizers/panels | `/services/layout/...` (see solaredgeoptimizers) `TBD` | — | 1/call | daily | `TBD` |
| Env benefits | ❌ not at `/environmentalBenefits` — path `TBD` | — | 1/call | daily | `TBD` |

## Rate-limiting & credit budget design

Reference account: **2000 credits/month**, **10 calls/min**. But the user can
have different limits, so both are **config inputs**, not constants.

### Inputs (from config/options flow)

- `plan_type`: `fleet` | `site_owner`
- `monthly_credit_budget`: int (default from plan, user-editable)
- `calls_per_minute`: int (default 10, user-editable)
- `budget_safety_factor`: float, default `0.7` (aim to use ≤70% of budget)
- `enabled_tiers` / enabled data domains (each adds credit cost)

### Adaptive interval algorithm (per config entry)

```
daily_budget      = monthly_budget * safety_factor / days_in_month
credits_per_cycle = sum(credit_cost of every endpoint an enabled tier calls)
cycles_per_day    = daily_budget / credits_per_cycle
base_interval     = 86400 / cycles_per_day           # seconds
interval          = clamp(base_interval, min_by_cpm, MAX_INTERVAL)
```

- `min_by_cpm` guards the per-minute limit: never schedule more than
  `calls_per_minute` calls in any 60s window across all tiers of the entry
  (and, for Fleet tokens, coordinate across all entries sharing the token).
- **Night slowdown:** multiply interval by `night_factor` (e.g. ×3) between
  sunset and sunrise for the fast tier (little production at night).
- **429 backoff:** exponential (e.g. 60s → 120s → … cap 1h); resume normal
  cadence after a clean cycle. Honor `Retry-After` if present.
- **Budget guard:** track rolling monthly spend in a persisted ledger; if
  projected end-of-month spend > budget, lengthen intervals and raise a HA
  repair issue.

### Cross-entry coordination (Fleet)

Multiple site entries can share one Fleet token and therefore one quota. Use a
**per-token shared ledger + rate gate** (keyed by token hash) in `hass.data` so
the combined call rate and monthly spend across all sites stay within limits.

### Open credit-model risk

If 1 credit ≠ 1 call (e.g. large time-series responses cost more), the ledger
must account by *declared endpoint cost*, and Phase 0 must nail down the exact
cost function. Until confirmed, assume **1 call = 1 credit** and keep the safety
factor conservative.
