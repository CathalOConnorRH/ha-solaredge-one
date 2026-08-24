"""Diagnostics support for SolarEdge ONE (secrets redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SolarEdgeOneConfigEntry
from .const import CONF_ACCOUNT_KEY, CONF_API_KEY

TO_REDACT = {CONF_API_KEY, CONF_ACCOUNT_KEY, "serialNumber", "serial_number"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SolarEdgeOneConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with credentials redacted."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    data = coordinator.data

    ledger = runtime.ledger
    diagnostics: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "ledger": {
            "monthly_budget": ledger.monthly_budget,
            "used": ledger.used,
            "remaining": ledger.remaining(),
            "month": ledger.month,
        },
        "rate_limit": {
            "limit_minute": getattr(runtime.client.rate_limit, "limit_minute", None),
            "remaining_minute": getattr(
                runtime.client.rate_limit, "remaining_minute", None
            ),
        },
        "devices": [
            async_redact_data(device.raw, TO_REDACT) for device in runtime.devices
        ],
    }

    if data is not None:
        diagnostics["data"] = {
            "overview": async_redact_data(data.overview.raw, TO_REDACT),
            "power_points": len(data.power.values),
            "power_latest": data.power.latest_value,
            "alert_count": len(data.alerts),
            "alerts": [async_redact_data(alert, TO_REDACT) for alert in data.alerts],
        }

    return diagnostics
