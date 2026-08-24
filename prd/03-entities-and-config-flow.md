# 03 — Data Model, Entities & Config Flow

## Device model (HA device registry)

Per site config entry, create a device tree:

- **Site** device (top-level) — identifier = site id.
- **Inverter** device(s) — `via_device` = site.
- **Battery/storage** device(s) — `via_device` = inverter or site.
- **Meter(s)** (production/consumption/import/export) — `via_device` = site.
- **Optimizers/panels** — optional child devices (can be many; make creation
  opt-in via options to avoid registry bloat and credit cost).

Use `has_entity_name = True` and stable `unique_id`s
(`{site_id}_{device_id}_{key}`).

## Entities (initial set — refine after Phase 0)

### Energy (Energy Dashboard — `state_class = total_increasing`)
- Solar production energy (Wh/kWh) — lifetime + today
- Consumption energy
- Grid import energy / export energy
- Self-consumption energy
- Battery charged / discharged energy

### Power (`state_class = measurement`)
- Current PV production power (W)
- Current consumption power
- Grid power (signed: import/export)
- Battery power (signed: charge/discharge)

### Battery/storage
- State of charge (%)
- Battery power (W)
- Backup reserve (%) — read-only initially
- Battery health / status

### Device-level
- Per-inverter: AC power, DC power, temperature, status
- Per-optimizer/panel: power, voltage, current (opt-in)

### Status & diagnostics (`binary_sensor` / `sensor`)
- Site online/connectivity (binary)
- Active alerts count + highest severity
- Last successful update timestamp (diagnostic)
- Credits used this month / remaining (diagnostic sensor — helps users tune)

> Map each entity to the exact API field + endpoint during Phase 0. Keep a
> field-mapping table in the library repo.

## Config flow

### Step: `user`
1. Choose **plan type**: `Fleet (My Fleet)` or `Site Owner`.
2. Enter credentials — v2 uses **headers**, not a bearer token:
   - `X-API-Key` (user key) — **required**; Phase 0 confirmed a token can
     authenticate with this key alone.
   - `X-Account-Key` (account/fleet key) — **optional**; likely needed only for
     Fleet/account-wide operations. Show as optional (or only for the Fleet
     plan-type choice).
3. (Optional advanced) monthly credit budget, calls/min, safety factor —
   pre-filled with plan defaults.
4. Validate credentials with the cheapest endpoint — likely `GET /sites`
   (confirm cost in Phase 0).

### Step: `select_site`
- Site list comes from `GET /sites` → `{"sites": {"count": N, "site": [
  {"siteId", "name", "peakPower", "activationStatus", ...} ]}}` (note the
  singular `site` array). Filter to `activationStatus == "ACTIVE"`.
- **Fleet:** present a multi-select of sites not already configured. Create
  **one config entry per selected site**. Set `unique_id = siteId` to prevent
  duplicates.
- **Site Owner:** resolve the single site automatically; if the token maps to
  multiple, present the picker.

### Step: `reauth`
- Triggered on `AuthError`. Re-prompt for token, keep site selection.

### Options flow
- Enabled data domains / tiers (production, battery, device-level, optimizers,
  alerts) — toggling adjusts credit cost and interval.
- Per-tier interval overrides (with budget warnings).
- Monthly budget, calls/min, safety factor, night-slowdown factor.
- Toggle: create per-optimizer/panel entities (default off).

## Diagnostics

- Redacted dump of: entry config (token redacted), coordinator state, credit
  ledger, last API errors, and per-tier intervals.

## Translations

- `strings.json` + `translations/en.json` for all flow steps, options, error
  keys, and entity names. Structure for easy addition of more languages.
