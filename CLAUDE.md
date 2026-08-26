# CLAUDE.md — ha-solaredge-one (Home Assistant integration)

Onboarding for an AI agent (or human) picking up this repo cold. Read this first,
then `prd/README.md` for the design docs.

## What this repo is

A **custom Home Assistant integration** (`domain: solaredge_one`) for SolarEdge's
v2 **"SolarEdge ONE"** monitoring API, superseding the legacy v1-based core
`solaredge` integration. Distributed **HACS-first**, built to HA-core standards,
with eventual HA Core submission in mind.

- **Read-only** (no control/write in v1).
- **One config entry per site.** Energy Dashboard is first-class.
- Supports both **Fleet** ("My Fleet") and **Site Owner** plans, chosen in the
  config flow.

## Two-repo layout (important)

The API client is a **separate PyPI package in its own repo** — this repo only
holds the HA integration.

| Repo | Path | Contains | Visibility |
|------|------|----------|------------|
| **Library** `aiosolaredge-one` | `../solaredge-v2` (`git@github.com:CathalOConnorRH/solaredge-v2.git`) | `src/aiosolaredge_one/` async client, models, budget math | PRIVATE |
| **Integration** `ha-solaredge-one` (this) | `github.com/CathalOConnorRH/ha-solaredge-one` | `custom_components/solaredge_one/` | PUBLIC |

`manifest.json` requires a pinned `aiosolaredge-one==X.Y.Z`; CI installs that
exact version **from PyPI**, same as HA/HACS will at runtime. If you change how
the integration calls the client, you likely need a matching change + release in
the library repo (see its `CLAUDE.md`), then bump the pin here.

## Dev environment

**Requires Python 3.13** (HA 2026.2.x needs 3.13; a 3.12 venv can't run the
harness). Bootstrap a local venv from a fresh clone with:

```bash
scripts/bootstrap.sh          # creates ./.venv (gitignored)
```

This installs `pytest-homeassistant-custom-component` (pulls in the right HA
version), `ruff`, and the `aiosolaredge-one` client. If a sibling
`../solaredge-v2` checkout exists it's installed **editable** (for cross-repo
work); otherwise the version pinned in `manifest.json` is installed from PyPI.
Overrides: `PYTHON=`, `VENV=`, `SOLAREDGE_LIB_PATH=` (see the script header).

Then, from this repo's root:

```bash
.venv/bin/python -m pytest tests -q                          # tests
.venv/bin/ruff check custom_components/solaredge_one tests   # lint (matches CI)
```

`pytest.ini`: `asyncio_mode = auto`, `testpaths = tests`. CI (`.github/workflows/
ci.yml`) runs ruff + pytest on 3.13 with the pinned library from PyPI.

> On the original maintainer's machine a **shared** venv at
> `../solaredge-v2/.venv-test` serves both repos; the bootstrap script's local
> `./.venv` is the portable, machine-agnostic equivalent — use whichever exists.
`.github/workflows/validate.yml` runs **hassfest + the HACS action** (both must
stay green; HACS currently has `ignore: brands` until a brands-icon PR lands).

## Code map (`custom_components/solaredge_one/`)

- `__init__.py` — setup/unload; builds the client, loads the persisted credit
  ledger, creates the coordinator, does a one-off `/devices` fetch and registers
  the device tree; typed `runtime_data` (`SolarEdgeOneRuntimeData`).
- `coordinator.py` — `DataUpdateCoordinator`. Per cycle fetches overview + power
  + alerts (`CREDITS_PER_CYCLE=3`), records spend in the ledger, and **recomputes
  its own `update_interval`** from remaining monthly budget (night ×3, 429 →
  exponential backoff, budget-guard repair issue). Also does a **throttled**
  `/energy` fetch (`ENERGY_REFRESH_INTERVAL`, daytime-only, cached) for the slow
  lifetime / this-year / this-month totals. Data container: `SolarEdgeOneData`
  (+ `EnergyTotals`).
- `config_flow.py` — plan-type step, `/sites` validation, Fleet site picker,
  single-site auto-create, reauth; options flow for budget/calls-per-minute/
  safety-factor with a reload listener.
- `sensor.py` — description-driven site sensors. Energy sensors are
  `total_increasing` Wh (device_class energy → Energy Dashboard); current power is
  a `measurement` W from `power.latest_value`. Breakdown + slow-total sensors are
  created only when the site reports a non-null value. Plus credit diagnostics and
  per-inverter optimizer-count.
- `binary_sensor.py` — site alerts (with raw payloads) + per-inverter connectivity.
- `entity.py` — base entities (`has_entity_name`, unique_id
  `{site_id}[_{serial}]_{key}`); `SolarEdgeOneEntity` + `SolarEdgeOneDeviceEntity`.
- `store.py` — persists the `CreditLedger` via HA `Store` (survives restart).
- `diagnostics.py` — redacts `api_key`, `account_key`, `serialNumber`.
- `const.py` — all tunables (intervals, budget defaults, credits-per-cycle,
  issue ids). `strings.json` == `translations/en.json` (keep them identical).

## Conventions / gotchas

- **Entity naming** is translation-key-driven: entity_id is
  `sensor.{device_slug}_{translated_name_slug}`. Renaming a sensor's display name
  in `strings.json`/`en.json` **changes its entity_id** — update tests accordingly.
- Overview totals are **today's running figures, not lifetime** (they reset at
  midnight; `total_increasing` handles that). Lifetime/YTD/MTD come only from the
  throttled `/energy` fetch.
- The `CreditLedger` only increments on real client HTTP calls. In tests the
  client is mocked, so **seed spend manually** via `coordinator.ledger.record(cost=N)`.
- Any test that runs a full coordinator refresh must mock **all** client calls:
  `get_site_overview`, `get_power`, `get_alerts`, `get_devices`, **`get_energy`**,
  and **`get_sites`** (install-date lookup). Missing a mock → non-awaitable
  MagicMock → the refresh fails.
- Keep `strings.json` and `translations/en.json` byte-identical.

## Release process (this repo)

1. Make changes + keep `tests`/`ruff` green.
2. Bump `custom_components/solaredge_one/manifest.json` `version`.
3. Commit, `git tag vX.Y.Z`, `git push origin main --tags`.
4. `gh release create vX.Y.Z --title vX.Y.Z --notes "..."`.

HACS installs from GitHub releases; hassfest/HACS validation run on push/PR.

## Current status & known blockers

- Live through **v0.3.0**: production today + breakdowns + current power + alerts
  + credit diagnostics + per-inverter connectivity/optimizers + lifetime/this-year/
  this-month totals. Config flow confirmed working against real HA. v0.3.0 wired
  the 0.3.0 library endpoints: environmental benefits (CO2 saved + EV miles),
  site-details metadata (seeds install date + site hw_version), lifetime energy
  via `resolution=TOTAL` (YEAR fallback), and battery storage telemetry.
- **Lifetime-via-TOTAL and storage telemetry are unverified against a live
  site:** both are guarded — TOTAL falls back to a YEAR-sum, and storage is
  battery-gated with defensive `parse_storage_state` that no-ops on an
  unrecognised payload. Diagnostics dump the raw storage payload so the real v2
  shape can be captured and the parser refined.
- **Still blocked (need real v2 API paths):** per-inverter *live* telemetry,
  per-optimizer/panel entities — these paths 404'd in probing.
- **Open tasks:** brands-icon PR to `home-assistant/brands` (needs 256×256 &
  512×512 PNGs, then drop `ignore: brands` in validate.yml); `hacs/default`
  listing PR; full non-en translations; quality-scale audit.

## Security

Never commit or store the SolarEdge API key. It lives only in gitignored
`.claude/settings.local.json`. Do not print it. (Owner has been advised to rotate
the key that was exposed in chat.)

## More context

- `prd/` — full design docs (`prd/README.md` is the index).
- `scripts/` — `phase0_capture.py` (endpoint discovery) and `probe_energy.py`
  (the `/energy` resolution probe used to design the lifetime sensors). Both read
  auth from env: `SOLAREDGE_API_KEY`, `SOLAREDGE_ACCOUNT_KEY`, `SOLAREDGE_BASE_URL`.
