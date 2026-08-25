# 06 — Home Assistant Quality Scale audit

Self-assessment against the HA [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
(52 rules across Bronze → Platinum). Statuses are **honest**:

- **done** — implemented and, where applicable, covered by tests.
- **todo** — a real gap we intend to close (see notes).
- **exempt** — genuinely not applicable to a read-only cloud-polling integration
  (with the reason).

We deliberately **do not** set the `quality_scale` field in `manifest.json` yet:
the Bronze `brands` rule requires assets merged into
[home-assistant/brands](https://github.com/home-assistant/brands), which is an
external PR that can't be completed from this repo. Once that lands, Bronze is
fully green and the tier can be declared.

> This audit lives as Markdown rather than a `quality_scale.yaml` because that
> file is validated by hassfest only for core integrations; shipping it in a
> custom component risks breaking the HACS/hassfest CI without adding value.
> It becomes a `quality_scale.yaml` at Core-submission time (Phase 7).

## Summary

| Tier | done | todo | exempt |
|------|-----:|-----:|-------:|
| Bronze | 15 | 1 (`brands`, external) | 2 |
| Silver | 9 | 0 | 1 |
| Gold | 14 | 4 | 3 |
| Platinum | 3 | 0 | 0 |

Bronze and Silver are effectively met (Bronze pending only the external `brands`
assets). Gold is largely met; the remaining gaps are device-lifecycle and
translated-exception work. Platinum is met.

## Bronze

| Rule | Status | Notes |
|------|--------|-------|
| action-setup | exempt | Read-only integration — registers no service actions. |
| appropriate-polling | done | Adaptive coordinator interval, credit-budget aware. |
| brands | todo | **External** — needs 256²/512² PNGs merged into home-assistant/brands. |
| common-modules | done | Coordinator in `coordinator.py`, base entities in `entity.py`. |
| config-flow | done | UI config flow with plan-type/token, Fleet site picker. |
| config-flow-test-coverage | done | `config_flow.py` at 100% line coverage. |
| dependency-transparency | done | `aiosolaredge-one` is open-source, on PyPI, pinned in the manifest. |
| docs-actions | exempt | No actions to document. |
| docs-high-level-description | done | README intro. |
| docs-installation-instructions | done | README → Installation (manual + HACS). |
| docs-removal-instructions | done | README → Removing the integration. |
| entity-event-setup | exempt | No external event subscriptions; `CoordinatorEntity` drives updates. |
| entity-unique-id | done | `{site_id}[_{serial}]_{key}`. |
| has-entity-name | done | All entities use `has_entity_name` + translation keys. |
| runtime-data | done | Typed `entry.runtime_data` dataclass. |
| test-before-configure | done | Config flow validates the token via `get_sites()`. |
| test-before-setup | done | First refresh raises `ConfigEntryAuthFailed` / `ConfigEntryNotReady`. |
| unique-config-entry | done | Unique ID per site; duplicate sites abort. |

## Silver

| Rule | Status | Notes |
|------|--------|-------|
| action-exceptions | exempt | No actions. |
| config-entry-unloading | done | `async_unload_entry` unloads platforms cleanly. |
| docs-configuration-parameters | done | README → Options table. |
| docs-installation-parameters | done | README → Configuration table. |
| entity-unavailable | done | `CoordinatorEntity` marks entities unavailable on failed cycles. |
| integration-owner | done | `codeowners` set in the manifest. |
| log-when-unavailable | done | Coordinator raises `UpdateFailed` (HA logs once, then on recovery). |
| parallel-updates | done | `PARALLEL_UPDATES = 0` on both platforms (read-only). |
| reauthentication-flow | done | `async_step_reauth[_confirm]` + tests. |
| test-coverage | done | ~97% overall, above the 95% target. |

## Gold

| Rule | Status | Notes |
|------|--------|-------|
| devices | done | Site device + child inverter devices with `via_device`. |
| diagnostics | done | `diagnostics.py`, credentials + serials redacted. |
| discovery | exempt | Cloud integration — nothing to discover on the local network. |
| discovery-update-info | exempt | No discovery. |
| docs-data-update | done | README → How it works (data updates). |
| docs-examples | done | README → Example automations. |
| docs-known-limitations | done | README → Known limitations. |
| docs-supported-devices | done | README → Supported devices. |
| docs-supported-functions | done | README → Entities. |
| docs-troubleshooting | done | README → Troubleshooting. |
| docs-use-cases | done | README → Use cases. |
| dynamic-devices | todo | Inverter inventory is fetched once at setup; new inverters appear after a reload. |
| entity-category | done | Diagnostic entities use `EntityCategory.DIAGNOSTIC`. |
| entity-device-class | done | `energy` / `power` device classes set. |
| entity-disabled-by-default | exempt | No high-cost/noisy entities that warrant being disabled by default. |
| entity-translations | done | `translation_key` + `strings.json` for all entities. |
| exception-translations | todo | Setup/coordinator exceptions raise English strings, not translation keys. |
| icon-translations | done | `icons.json` covers every entity translation key. |
| reconfiguration-flow | todo | Only reauth exists; no `async_step_reconfigure` to change site/plan in place. |
| repair-issues | done | `over_budget` repair issue raised/cleared from the month-end projection. |
| stale-devices | todo | Removed inverters are not auto-pruned from the device registry. |

## Platinum

| Rule | Status | Notes |
|------|--------|-------|
| async-dependency | done | `aiosolaredge-one` is fully async. |
| inject-websession | done | Client is given HA's shared `async_get_clientsession` (setup + config flow). |
| strict-typing | done | `mypy --strict` clean, enforced in CI. |

## Path to close the remaining gaps

1. **brands** (Bronze) — produce logo/icon PNGs, PR to home-assistant/brands,
   then remove `ignore: brands` from `validate.yml` and set `quality_scale` in
   the manifest.
2. **dynamic-devices** + **stale-devices** (Gold) — re-fetch the `/devices`
   inventory periodically and add/remove devices from the registry on change.
3. **reconfiguration-flow** (Gold) — add `async_step_reconfigure` to change the
   plan/site without deleting the entry.
4. **exception-translations** (Gold) — move setup/coordinator error messages to
   `strings.json` translation keys.
