# 04 — Roadmap & Milestones

Phases are ordered so each ends with something testable. Credit spend is
concentrated in Phase 0; later phases run on fixtures.

## Phase 0 — API capture & spike (credits budgeted, logged)
- Fill the [02 capture checklist](02-api-and-rate-limiting.md): auth, base URLs,
  versioning, rate-limit headers, credit model, endpoint list.
- Record 1 fixture JSON per endpoint (secrets redacted) into the library repo.
- Confirm the exact credit cost model (1 call = 1 credit? or by payload?).
- **DoD:** endpoint reference table filled; fixtures committed; credit-model
  documented; total spend logged and < 100 credits.

## Phase 1 — API client library (`aiosolaredge-one`)  🟡 IN PROGRESS
- [x] Async client (`aiosolaredge-one/`), `X-API-Key` (+ optional `X-Account-Key`) auth.
- [x] Typed models (Site, SiteOverview, Device, TimeSeries) + exceptions
      (Auth/RateLimit/NotFound/Connection/Api).
- [x] Credit accounting (`CreditLedger`, local monthly) + rate-limit header
      parsing (`RateLimit`); 429 raises `SolarEdgeRateLimitError(retry_after)`.
- [x] Methods for all 6 confirmed endpoints (sites, overview, devices, energy,
      power, alerts) with unit tests against Phase 0 fixtures.
- [x] `ruff` clean + `mypy --strict` clean + 18 tests passing (no live API).
- [ ] Add endpoints for battery/storage + optimizer telemetry once doc paths known.
- [x] Async retry/backoff helper — `budget.backoff_interval` (coordinator drives it).
- [x] CI workflow (`.github/workflows/ci.yml`: ruff + mypy + pytest, lib & integration).
- [x] Publish pipeline ready — Trusted Publishing (`.github/workflows/publish.yml`),
      `LICENSE`/`CHANGELOG`/URLs complete, `python -m build` + `twine check` pass,
      wheel installs clean in a fresh venv. See `aiosolaredge-one/RELEASING.md`.
- [ ] Execute publish: repo pushed to `CathalOConnorRH/solaredge-v2` (private);
      still needs PyPI/TestPyPI trusted publishers configured, dry-run to
      TestPyPI, then release `0.2.0` to PyPI (see `aiosolaredge-one/RELEASING.md`).
- **DoD:** every captured endpoint has a tested method; `mypy --strict` clean. ✅ (core met)

## Phase 2 — Integration skeleton + config flow  ✅ DONE
- [x] `manifest.json`, domain `solaredge_one`, HACS metadata (`hacs.json`).
- [x] Config flow: plan-type + token + validate via `/sites`; Fleet site picker
      (multi-site) → one entry per site; single-site auto-creates; reauth.
- [x] `DataUpdateCoordinator` + typed `runtime_data`; setup/unload; auth error →
      `ConfigEntryAuthFailed` (SETUP_ERROR / reauth).
- [x] Tests (`tests/`, HA harness on Py3.13): 8 passing — single/multi-site,
      invalid_auth, cannot_connect, no_sites/already-configured, setup+unload,
      auth-failure. `ruff` clean; strings↔translations parity; modules import.
- **DoD:** ✅ Fleet + Site Owner both addable via UI (mocked client);
  config-flow tests pass; entries unload cleanly.

## Phase 3 — Coordinators + adaptive rate limiting  ✅ DONE
- [x] Adaptive interval algorithm — pure, remaining-budget-aware pacing in the
      library (`aiosolaredge_one.budget`: `compute_interval`/`plan_interval`,
      `BudgetPlan`, `project_month_end_usage`, `backoff_interval`). Self-correcting
      so it doubles as the budget guard. 10 lib tests incl. simulated-month.
- [x] Coordinator recomputes its interval each cycle from credits left in the
      month; night slowdown (×3 via `sun.is_up`); per-minute floor enforced.
- [x] 429 → exponential backoff (1 min → cap 1 h, honours `Retry-After`),
      `UpdateFailed` (no crash), auto-recovers on next clean cycle.
- [x] Persisted `CreditLedger` via HA `Store` (`store.py`) — restored on setup,
      saved each successful cycle; survives restart.
- [x] Budget guard raises/clears an `over_budget` repair issue from the
      month-end projection.
- [x] Options flow (`monthly_credit_budget`, `calls_per_minute`,
      `budget_safety_factor`) + reload-on-change listener.
- [x] Library bumped to `0.2.0`; manifest requirement + strings/translations
      updated. Integration tests: 14 passing (backoff, ledger round-trip,
      pacing bounds, budget issue, night>day). ruff + mypy clean both packages.
- **DoD:** ✅ simulated month stays under budget (`test_budget.py`); 429 injection
  backs off without crashing (`test_coordinator.py`); restart restores the ledger.
- Deferred to Phase 4/5 (needs entities): tiered fast/slow/daily coordinators and
  cross-entry shared ledger/rate-gate for Fleet tokens sharing one quota.

## Phase 4 — Entities: energy, power, battery  ✅ DONE (battery pending API paths)
- [x] `sensor` platform (`sensor.py`) with description-driven entities:
      lifetime production + current power (always), plus grid/self-consumption/
      storage and consumption breakdown sensors created **only** when the site
      reports them (PV-only sites skip them). Energy sensors are `total_increasing`
      Wh with `device_class=energy` → Energy Dashboard ready.
- [x] Base entity (`entity.py`, `has_entity_name`, `unique_id`
      `{site_id}[_{serial}]_{key}`); Site device + child devices registered in
      `__init__._register_devices` (via_device tree; inventory from one-off
      `/devices` fetch at setup).
- [x] Tests (`tests/test_sensor.py`): PV-only vs metered+battery sites, night
      (all-null power → unknown), device tree (site↔inverter via_device).
- [ ] Battery/storage energy + power sensors — blocked on real v2 storage paths
      (404 in Phase 0). Fields are already modelled and rendered conditionally.
- **DoD:** ✅ Energy Dashboard shows production/import/export from fixtures;
  entity tests pass. (Battery deferred until API paths are known.)

## Phase 5 — Device-level + status/alerts  ✅ DONE (live per-inverter telemetry pending)
- [x] Alerts `binary_sensor` (site `problem`, exposes raw payloads); coordinator
      now fetches `/alerts` each cycle (3 credits/cycle), tolerant of 404.
- [x] Credits used / remaining diagnostic sensors (local ledger, no API cost).
- [x] Per-inverter diagnostics: connectivity `binary_sensor` + connected-optimizer
      count sensor (from the `/devices` inventory), attached to the inverter device.
- [x] Diagnostics dump (`diagnostics.py`) with credentials + serials redacted.
- [x] Tests (`test_binary_sensor.py`, `test_diagnostics.py`).
- [ ] Per-inverter **live** telemetry + opt-in per-optimizer/panel entities —
      blocked on the device-telemetry v2 paths (only inventory is confirmed).
- **DoD:** ✅ device tree correct; alerts reflect fixtures; diagnostics redacted.

## Phase 6 — Polish & release  ✅ DONE (external `brands` + full i18n deferred)
- [x] Repo `README.md` — install, config, options, entities, **plus the Gold
      `docs-*` sections**: supported devices, data-update model, use cases,
      example automations, known limitations, troubleshooting, removal.
      `codeowners` set in the manifest.
- [x] Options flow for the core tunables (budget / calls-per-minute / safety).
- [x] Quality-scale audit — see [06-quality-scale.md](06-quality-scale.md).
      Bronze/Silver effectively met (Bronze pending only external `brands`
      assets); Gold largely met; Platinum met (`async` lib, injected websession,
      `mypy --strict` in CI). `PARALLEL_UPDATES = 0`, `icons.json`, and mypy in
      CI added here.
- [x] `config_flow.py` at 100% coverage; overall ~97% (above the 95% target).
- [ ] HACS release tag — **blocked**: repo is private; needs to be made public.
- [ ] `brands` assets (PR to home-assistant/brands); full translations beyond
      `en`; Gold `dynamic-devices`/`stale-devices`/`reconfiguration-flow`/
      `exception-translations` (tracked in 06-quality-scale.md).
- **DoD:** ✅ docs complete; coverage target met; quality-scale audited. HACS
  tag still awaits the repo going public.

## Phase 7 — HA Core submission (stretch)
- Library already separate; align with core requirements; open PR.
- Convert [06-quality-scale.md](06-quality-scale.md) into `quality_scale.yaml`
  and set the manifest `quality_scale` tier once `brands` has landed.

## Cross-cutting engineering standards
- Python 3.13+, async everywhere, `ruff` + `mypy --strict`.
- `pytest` with `pytest-homeassistant-custom-component`; fixtures for all API IO;
  **no live calls in CI**.
- Every live dev call during Phase 0 is logged (endpoint, credits, timestamp).
