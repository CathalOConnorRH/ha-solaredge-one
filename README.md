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

## Entities

**Site device**

- Lifetime production (energy, `total_increasing`)
- Current power (power, `measurement`)
- Exported/imported, self-consumption, and storage flows *(only when your site
  reports them — a PV-only site without a meter or battery won't create these)*
- Alerts (`binary_sensor`, exposes the raw alert payloads)
- Credits used / remaining this month *(diagnostic)*

**Per inverter**

- Connectivity *(diagnostic)*
- Connected optimizers *(diagnostic)*

## Development

Requires **Python 3.13** (the Home Assistant test harness needs it). The client
library lives in its own repo and is published to PyPI:

```bash
# The pinned release (matches manifest.json):
pip install "aiosolaredge-one==0.2.0"
# ...or an editable checkout of the library repo (sibling directory) when
# developing both together:
pip install -e ../solaredge-v2

pip install pytest-homeassistant-custom-component ruff
ruff check custom_components/solaredge_one tests
pytest tests/
```

CI (`.github/workflows/ci.yml`) runs ruff + pytest on every push. See
[`prd/04-roadmap.md`](prd/04-roadmap.md) for the phased plan.

## Status

Actively developed. Production/power, alerts, adaptive rate-limiting, diagnostics
and the device tree are implemented. Battery/storage detail and per-inverter live
telemetry are pending confirmation of the corresponding v2 API paths.

## License

MIT.
