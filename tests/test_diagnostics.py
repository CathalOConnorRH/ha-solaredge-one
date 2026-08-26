"""Diagnostics redaction tests (Phase 5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from aiosolaredge_one import (
    ConsumptionOverview,
    Device,
    EnvironmentalBenefits,
    ProductionOverview,
    Site,
    SiteOverview,
    TimeSeries,
    TimeValue,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaredge_one.const import (
    CONF_ACCOUNT_KEY,
    CONF_API_KEY,
    CONF_PLAN_TYPE,
    CONF_SITE_ID,
    DOMAIN,
    PLAN_FLEET,
)
from custom_components.solaredge_one.diagnostics import (
    async_get_config_entry_diagnostics,
)

SITE_ID = 3066774


def _overview() -> SiteOverview:
    return SiteOverview(
        site_id=SITE_ID,
        production=ProductionOverview(total=26290.0, unit="WH"),
        consumption=ConsumptionOverview(),
        raw={"siteId": SITE_ID, "production": {"total": 26290.0}},
    )


def _power() -> TimeSeries:
    return TimeSeries(
        period_from=None,
        period_to=None,
        unit="W",
        resolution="QUARTER_HOUR",
        values=[TimeValue(timestamp="t", value=123.0)],
    )


async def test_diagnostics_redacts_secrets(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(SITE_ID),
        title="My Home",
        data={
            CONF_PLAN_TYPE: PLAN_FLEET,
            CONF_API_KEY: "super-secret-key",
            CONF_ACCOUNT_KEY: "super-secret-account",
            CONF_SITE_ID: SITE_ID,
        },
    )
    entry.add_to_hass(hass)

    device = Device(
        type="INVERTER",
        serial_number="SECRET-SERIAL",
        name="Inverter 1",
        active=True,
        raw={"type": "INVERTER", "serialNumber": "SECRET-SERIAL"},
    )
    with patch("custom_components.solaredge_one.SolarEdgeOneClient") as mock_cls:
        client = mock_cls.return_value
        client.get_site_overview = AsyncMock(return_value=_overview())
        client.get_power = AsyncMock(return_value=_power())
        client.get_alerts = AsyncMock(return_value=[{"id": 1, "message": "x"}])
        client.get_devices = AsyncMock(return_value=[device])
        client.get_site_details = AsyncMock(
            return_value=Site(site_id=SITE_ID, name="My Home")
        )
        client.get_lifetime_energy = AsyncMock(return_value=_power())
        client.get_energy = AsyncMock(return_value=_power())
        client.get_environmental_benefits = AsyncMock(
            return_value=EnvironmentalBenefits()
        )
        client.get_sites = AsyncMock(return_value=[])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # The mocked client bypasses the real HTTP path, so simulate credit spend.
    entry.runtime_data.ledger.record(cost=3)
    diag = await async_get_config_entry_diagnostics(hass, entry)

    # Credentials must be redacted, not present in the clear.
    assert diag["entry"]["data"][CONF_API_KEY] != "super-secret-key"
    assert diag["entry"]["data"][CONF_ACCOUNT_KEY] != "super-secret-account"
    # Device serials redacted too.
    assert "SECRET-SERIAL" not in str(diag["devices"])

    # Non-secret operational data is present.
    assert diag["ledger"]["used"] == 3
    assert diag["data"]["alert_count"] == 1
    assert diag["data"]["power_latest"] == 123.0
