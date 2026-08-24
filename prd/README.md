# SolarEdge ONE — Home Assistant Integration (v2)

PRD set for a new Home Assistant integration built on the **SolarEdge ONE for
developers** platform (the successor to the legacy v1 Monitoring API).

Docs: https://api-docs.solaredge.com/docs/developer-platform/xfdh5szlltdtn-welcome-to-solar-edge-one-for-developers

## Why this exists

The existing HA core `solaredge` integration is built on the **legacy v1
Monitoring API** (single site, one account API key, request-count rate limits).
SolarEdge is rolling out **SolarEdge ONE**, a new developer platform with a
credit-based quota model and (at least) two token types: **Fleet ("My Fleet")**
and **Site Owner**. This project is a clean-room *v2* integration targeting the
new platform, with multi-site fleet support, device-level telemetry, battery
data, alerts, and first-class Energy Dashboard support.

## How to read these files

| File | Purpose |
|------|---------|
| [00-overview.md](00-overview.md) | Goals, scope, non-goals, success criteria, personas |
| [01-architecture.md](01-architecture.md) | Component layout, coordinator design, library split |
| [02-api-and-rate-limiting.md](02-api-and-rate-limiting.md) | **API capture checklist** + credit budget + adaptive polling |
| [03-entities-and-config-flow.md](03-entities-and-config-flow.md) | Data model, entities, config/options/reauth flows, multi-site |
| [04-roadmap.md](04-roadmap.md) | Phased milestones, definition of done per phase |
| [05-open-questions.md](05-open-questions.md) | Unresolved decisions + assumptions to confirm |

## Decisions locked (from requirements grilling, 2026-08-24)

1. **Distribution:** HACS custom component first, built to HA-core quality
   standards, with HA Core submission as a later goal.
2. **Plans supported:** Fleet API ("My Fleet") **and** Site Owner API, selected
   via a config-flow option. (Legacy v1 Monitoring API is **out of scope**.)
3. **Data scope:** production/consumption, battery/storage, device-level
   (per-inverter and per-optimizer/panel), and status/alerts.
4. **Rate strategy:** adaptive polling — the coordinator derives a safe interval
   from the user's stated plan limits and entity count, slows at night, and
   backs off on HTTP 429.
5. **Site mapping:** one HA config entry per site.
6. **Energy Dashboard:** first-class (`total_increasing` energy sensors with
   correct `device_class`/`state_class`).
7. **Dev data policy:** limited live API calls are permitted during development,
   staying well under budget, with every call logged. Prefer recorded fixtures.
8. **API client:** shipped as a **separate PyPI library**, consumed by the
   integration (eases eventual HA Core submission).

## Key references

- v2 base URL: `https://monitoringapi.solaredge.com/v2` (header auth:
  `X-Account-Key` + `X-API-Key`). See [02](02-api-and-rate-limiting.md).
- Official API docs (Stoplight SPA): `https://se-api.stoplight.io/docs/monitoring/`
- v1→v2 migration guide: https://api-docs.solaredge.com/docs/basic-monitoring-api/m32bx376ka8mb-migrating-from-v1-to-v2
- Prior art: `talbronfer/monitoring-v2-api-demo` (quickstart),
  `AndrewTapp/solaredgeoptimizers` (ONE-aware HACS integration).

## Confirmed (2026-08-24)

- Integration domain: `solaredge_one` ✅
- API client library name: `aiosolaredge-one` (async, aiohttp-based) ✅
- Scope: **read-only** — no control/write features (e.g. set backup reserve) in v1 ✅

## Working assumptions (confirm — see 05-open-questions.md)

- Language/runtime: Python 3.13+, `async`/`await` throughout
- Target HA version: current stable at build time (~2026.8+); min floor TBD
