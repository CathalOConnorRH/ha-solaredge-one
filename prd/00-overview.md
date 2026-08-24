# 00 — Product Overview

## Vision

A modern, well-behaved Home Assistant integration for SolarEdge systems built on
the **SolarEdge ONE** developer platform, that respects the platform's tight
credit budget while surfacing rich production, consumption, battery, device, and
alert data — and works cleanly with the HA Energy Dashboard.

## Goals

- **G1 — New platform.** Use SolarEdge ONE APIs (not legacy v1 Monitoring).
- **G2 — Both token types.** Support Fleet ("My Fleet") and Site Owner tokens,
  chosen by the user during setup.
- **G3 — Rich data.** Expose production/consumption, battery/storage,
  device-level (inverter + optimizer/panel), and status/alerts.
- **G4 — Budget-safe.** Never blow the user's monthly credit budget or the
  per-minute call limit; degrade gracefully rather than error.
- **G5 — Energy Dashboard native.** Provide statistics-grade energy sensors.
- **G6 — Core-ready.** Build to HA-core quality standards from day one so a
  later core submission is a small delta.

## Non-goals (initial release)

- Legacy v1 Monitoring API support.
- Local/Modbus (LAN) polling — that is served by `solaredge_modbus`/`solaredge-modbus-multi`.
- Write/control operations (e.g. setting backup reserve) unless the ONE API
  exposes them cheaply and safely — deferred to a later phase.
- Non-solar SolarEdge ONE products (EV chargers, etc.) — evaluate later.

## Personas

- **Fleet installer** — has a My Fleet token (2000 credits/mo, 10 calls/min in
  the reference account), manages many sites, wants to monitor several in HA.
- **Site owner** — a homeowner with a Site Owner token and a single site.

## Success criteria

- **SC1** Setup completes via UI config flow with a token + plan-type choice;
  no YAML.
- **SC2** For a Fleet token, the user can pick which site(s) to add; each site
  becomes its own config entry.
- **SC3** Steady-state credit consumption stays under the user's stated monthly
  budget with margin (target: ≤ 70% of budget at default settings).
- **SC4** HTTP 429 / quota-exhaustion never crashes the integration; entities go
  `unavailable` and recover automatically.
- **SC5** Energy Dashboard shows solar production, grid import/export, and (if
  present) battery in/out from this integration's sensors.
- **SC6** Passes HA config-flow, unload, and reauth tests; `strict` typing and
  `ruff` clean.

## Quality scale target

Aim for HA **Silver→Gold** equivalents even as a custom component:
config-entry-only, unique IDs, reauth, options flow, full test coverage,
translations, `has_entity_name`, device registry, diagnostics, and
`parallel_updates` discipline.
