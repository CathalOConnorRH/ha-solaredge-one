# SolarEdge ONE for Home Assistant

A Home Assistant integration for the **SolarEdge ONE** developer platform (the
v2 Monitoring API), the successor to the legacy v1-based core `solaredge`
integration.

- **Credit-aware adaptive polling** — the integration paces itself to your
  plan's monthly credit budget and per-minute call limit, slows down at night,
  and backs off on rate limits, so it never blows through your quota.
- **Energy Dashboard first-class** — cumulative production/import/export/storage
  energy sensors with the correct device/state classes.
- **Fleet _and_ Site Owner** tokens, chosen in the config flow. One config entry
  per site; multi-site Fleet accounts add each site separately.
- **Alerts, diagnostics, and per-inverter inventory** surfaced as entities.

> **Read-only.** This integration does not control or write to your system
> (no setting backup reserve, etc.).

The async API client is a **separate package**,
[`aiosolaredge-one`](https://github.com/CathalOConnorRH/solaredge-v2), published
to PyPI and pinned by this integration's `manifest.json`.

## Installation

The API client library is on PyPI, so Home Assistant installs it automatically
(it's pinned in `manifest.json`) — you only need to get the integration files
into your config. Pick whichever route suits you.

### Option A — Manual copy (quickest for testing, no HACS needed)

1. Copy the folder [`custom_components/solaredge_one/`](custom_components/solaredge_one)
   into your Home Assistant config directory (the one with `configuration.yaml`)
   so you end up with `<config>/custom_components/solaredge_one/`.
   - **HA OS / Supervised:** use the *Samba*, *SSH & Web Terminal*, or
     *Studio Code Server* add-on to reach the config folder.
   - **Container / Core:** copy it into the mounted config volume directly.
2. **Restart Home Assistant** (Settings → System → Restart) — HA scans
   `custom_components/` and installs the library from PyPI on startup.
3. Add the integration:

   [![Open your Home Assistant instance and start setting up this integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=solaredge_one)

   …or go to **Settings → Devices & Services → Add Integration → SolarEdge ONE.**

### Option B — HACS

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=CathalOConnorRH&repository=ha-solaredge-one&category=integration)

1. Click the button above (or in HACS → ⋮ → **Custom repositories**, add
   `https://github.com/CathalOConnorRH/ha-solaredge-one` as an *Integration*).
2. Install **SolarEdge ONE** and **restart Home Assistant**.
3. Add the integration with the *config-flow* button above, or via
   **Settings → Devices & Services → Add Integration → SolarEdge ONE.**

> The HACS button and custom-repository flow require this repo to be **public**.
> While it's private, use **Option A** (manual copy) — that works regardless of
> repo visibility. Note the *config-flow* button in Option A only works after the
> integration files are already installed and HA has restarted.

## Configuration

You'll need a SolarEdge ONE API key from the
[developer platform](https://api-docs.solaredge.com/docs/developer-platform/xfdh5szlltdtn-welcome-to-solar-edge-one-for-developers).

| Field | Notes |
|-------|-------|
| **Plan type** | *Fleet (My Fleet)* or *Site owner* |
| **API key** (`X-API-Key`) | Required for both plan types |
| **Account key** (`X-Account-Key`) | Fleet accounts only |

For a Fleet token you then pick which site to add; run the flow again to add
more sites.

### Options — rate limiting & credit budget

Under the integration's **Configure** button:

| Option | Default | What it does |
|--------|---------|--------------|
| **Monthly credit budget** | 2000 | Total API credits your plan allows per month |
| **Calls per minute** | 10 | Per-minute cap your plan allows |
| **Budget safety factor** | 0.7 | Fraction of the budget to actually spend, leaving headroom |

The integration recomputes its polling interval every cycle from the credits
still available for the rest of the month. If projected month-end spend would
exceed your budget it raises a repair issue and slows polling automatically.

## Supported devices

This is a **cloud** integration — it talks to the SolarEdge ONE monitoring API,
not to hardware on your network, so there is nothing to discover locally.

- **Any SolarEdge site** visible to your API token (Fleet or Site Owner).
- **Inverters** in that site are registered as child devices (from the one-off
  device inventory), with connectivity + connected-optimizer diagnostics.
- Meter- and battery-derived figures (grid import/export, self-consumption,
  storage flows) appear **only when your site actually reports them**. A PV-only
  site without a meter or battery simply won't get those entities.

## Entities

**Site device**

- **Production today** (energy, `total_increasing`) — today's running total; resets at midnight.
- **Lifetime production**, **Production this year**, **Production this month**
  (energy, `total_increasing`) — from the `/energy` endpoint; created only when available.
- **Current power** (power, `measurement`).
- **Exported to grid**, **Imported from grid**, **Self-consumption**,
  **Consumption**, **Consumption from solar**, **Consumption from storage**,
  **Production to storage** — all *today* totals, created only when your site reports them.
- **Alerts** (`binary_sensor`, `problem`) — on when the site has open alerts; the raw payloads are an attribute.
- **Credits used / remaining this month** *(diagnostic)*.

**Per inverter**

- **Connectivity** *(diagnostic)*.
- **Connected optimizers** *(diagnostic)*.

## How it works (data updates)

The integration polls on a **single adaptive interval** that it recomputes after
every cycle from the credits still available for the rest of the month, so it
self-paces to your plan instead of using a fixed interval:

- Each cycle spends ~3 credits (overview + power + alerts). It **slows down at
  night** (nothing is produced) and **backs off** on HTTP 429 rate limits.
- The slow lifetime / this-year / this-month totals come from a **separate
  `/energy` fetch, throttled to once an hour** (daytime only) and cached between
  cycles, so they add only a couple of credits per hour.
- If projected month-end spend would exceed your budget, a **repair issue** is
  raised and polling slows automatically. Tune the budget in the options.

Energy sensors are `total_increasing`, which is exactly what the **Energy
Dashboard** wants (it handles the midnight reset of the daily totals for you).

## Use cases

- Feed **production, grid import/export, and self-consumption** into the Home
  Assistant **Energy Dashboard**.
- Track **lifetime / yearly / monthly** production alongside today's figures.
- Get notified when the site raises an **alert**, or when an **inverter drops
  offline**, via the connectivity/alerts sensors.
- Keep an eye on **API credit usage** with the diagnostic sensors so you stay
  within your plan.

## Example automations

Notify when the site raises an alert:

```yaml
automation:
  - alias: "SolarEdge alert notification"
    triggers:
      - trigger: state
        entity_id: binary_sensor.my_home_alerts
        to: "on"
    actions:
      - action: notify.notify
        data:
          title: "SolarEdge alert"
          message: >-
            {{ state_attr('binary_sensor.my_home_alerts', 'alert_count') }} open
            alert(s) on your solar site.
```

Warn if you're on track to exceed the API credit budget (uses the built-in
repair issue, or the diagnostic sensor directly):

```yaml
automation:
  - alias: "SolarEdge credits running low"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.my_home_credits_remaining_this_month
        below: 100
    actions:
      - action: persistent_notification.create
        data:
          title: "SolarEdge API credits low"
          message: "Fewer than 100 API credits remain this month."
```

*(Entity IDs depend on your site name — check Developer Tools → States.)*

## Known limitations

- **Read-only**: no control/write actions (backup reserve, exports, etc.).
- **Battery/storage detail** and **per-inverter live telemetry** (per-optimizer /
  per-panel) are not exposed yet — the corresponding v2 API paths are not
  publicly confirmed. Battery-derived *aggregate* flows still appear when the
  overview reports them.
- The **monthly credit quota is not returned by the API**, so it's tracked
  locally from your configured budget; if you spend credits from other apps the
  local ledger won't see them.
- The device inventory is fetched **once at setup** — added/removed inverters
  appear after you reload the entry.

## Troubleshooting

- **"Invalid authentication"** — check the API key (and, for Fleet tokens, the
  account key). Use the integration's **reauth** prompt to enter a new key.
- **A "credit budget may be exceeded" repair issue** — polling has been slowed
  automatically; raise the **Monthly credit budget** in the options if your plan
  actually allows more, or lower the **safety factor** for more headroom.
- **Missing grid/battery sensors** — expected on a PV-only site; those entities
  are only created when the site reports the data.
- **Diagnostics**: from the integration's ⋮ menu choose **Download diagnostics**
  (credentials and serials are redacted) when reporting an issue.

## Removing the integration

Delete each SolarEdge ONE entry from **Settings → Devices & Services**; that
removes its devices, entities, and the stored credit ledger. If you installed via
HACS and no longer want the code, remove it from HACS too (or delete
`custom_components/solaredge_one/` for a manual install) and restart. Consider
revoking the API token in the SolarEdge developer portal.

## Development

Requires **Python 3.13** (the Home Assistant test harness needs it). Bootstrap a
local venv from a fresh clone:

```bash
scripts/bootstrap.sh          # creates ./.venv (see CLAUDE.md for details)

.venv/bin/ruff check custom_components/solaredge_one tests
.venv/bin/mypy --strict custom_components/solaredge_one
.venv/bin/python -m pytest tests -q
```

The async API client lives in its own repo,
[`aiosolaredge-one`](https://github.com/CathalOConnorRH/solaredge-v2) (installed
from PyPI, or editable from a sibling `../solaredge-v2` checkout when developing
both together). CI (`.github/workflows/ci.yml`) runs ruff + mypy + pytest;
`.github/workflows/validate.yml` runs hassfest + HACS validation. See
[`prd/04-roadmap.md`](prd/04-roadmap.md) for the phased plan and
[`prd/06-quality-scale.md`](prd/06-quality-scale.md) for the quality-scale audit.

## Status

Actively developed. Production/power, lifetime/year/month energy, alerts,
adaptive rate-limiting, diagnostics and the device tree are implemented.
Battery/storage detail and per-inverter live telemetry are pending confirmation
of the corresponding v2 API paths.

## License

MIT.
