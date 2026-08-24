# 01 — Architecture

## Two-package split

```
aiosolaredge-one/            # separate PyPI library (async client)
  aiosolaredge_one/
    client.py                # SolarEdgeOneClient (aiohttp session injected)
    auth.py                  # token handling for Fleet vs Site Owner
    endpoints/               # thin typed wrappers per API area
    models.py                # dataclasses / TypedDicts for responses
    ratelimit.py             # credit accounting + 429 backoff primitives
    exceptions.py            # AuthError, RateLimitError, ApiError, ...
    const.py

homeassistant custom_components/solaredge_one/   # the integration
  __init__.py                # setup/unload, coordinator wiring
  config_flow.py             # user + reauth + options flows
  coordinator.py             # DataUpdateCoordinator(s), adaptive interval
  entity.py                  # base entity (device_info, has_entity_name)
  sensor.py                  # sensors (energy, power, battery, device, status)
  binary_sensor.py           # alerts / connectivity
  diagnostics.py
  manifest.json / strings.json / translations/
```

Rationale: HA Core requires the protocol/client code to live in a published
library. Building it separately now (per locked decision #8) makes the eventual
core PR a thin wrapper.

## Client library principles

- Fully `async`, `aiohttp`-based; caller injects the `ClientSession`.
- **No** internal reton-forever loops; surface typed exceptions and let HA's
  coordinator decide.
- Built-in **credit accounting**: each endpoint method declares its credit cost
  (see 02) so callers can budget before calling.
- Pluggable auth strategy for `Fleet` vs `SiteOwner` (different header/param and
  possibly different base path).
- No HA imports — usable standalone; ships its own fixtures for tests.

## Coordinator design

- **One config entry per site** → each entry owns its coordinator(s).
- Consider **tiered coordinators** with different intervals to save credits:
  - *Fast tier* — live power / battery SoC (cheapest, most time-sensitive).
  - *Slow tier* — cumulative energy, device inventory, status/alerts.
  - *Daily tier* — site metadata, environmental benefits, device lists.
- The adaptive-interval logic (02) sets each tier's interval from: plan limits,
  number of enabled tiers/entities, and time of day (night slowdown).
- All tiers share a single **credit ledger** per config entry so combined usage
  respects the monthly budget.

## Data flow

1. Config flow validates token + plan type, lists sites (Fleet) or resolves the
   single site (Site Owner), user selects site → creates entry.
2. `__init__.async_setup_entry` builds the client, restores the credit ledger
   from entry storage, instantiates coordinators.
3. Coordinators fetch on their adaptive schedule; on 429 or quota-low they back
   off and mark data stale.
4. Entities read coordinator data; energy sensors also feed long-term stats.

## State persistence

- Persist the **credit ledger** (rolling monthly usage, reset day) so restarts
  don't lose budget accounting. Use HA `Store` (helpers.storage) keyed by entry.
- Persist last-known-good values where sensible so restarts don't show gaps
  before the first refresh.

## Error handling

- `AuthError` → trigger HA reauth flow.
- `RateLimitError` (429) → exponential backoff, entities `unavailable`, no crash.
- Budget-guard: if projected monthly usage would exceed the user's stated cap,
  automatically lengthen intervals and log a warning / raise a repair issue.
