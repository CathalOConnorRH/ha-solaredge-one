"""Binary sensor + credit/diagnostic sensor tests (Phase 5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from aiosolaredge_one import (
    ConsumptionOverview,
    Device,
    ProductionOverview,
    SiteOverview,
    TimeSeries,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaredge_one.const import (
    CONF_API_KEY,
    CONF_PLAN_TYPE,
    CONF_SITE_ID,
    DOMAIN,
    PLAN_SITE_OWNER,
)

SITE_ID = 3066774


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(SITE_ID),
        title="My Home",
        data={
            CONF_PLAN_TYPE: PLAN_SITE_OWNER,
            CONF_API_KEY: "test-key",
            CONF_SITE_ID: SITE_ID,
        },
    )


def _overview() -> SiteOverview:
    return SiteOverview(
        site_id=SITE_ID,
        production=ProductionOverview(total=26290.0, unit="WH"),
        consumption=ConsumptionOverview(),
    )


def _power() -> TimeSeries:
    return TimeSeries(
        period_from=None, period_to=None, unit="W", resolution="QUARTER_HOUR", values=[]
    )


async def _setup(
    hass: HomeAssistant,
    *,
    alerts: list[dict] | None = None,
    devices: list[Device] | None = None,
) -> MockConfigEntry:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch("custom_components.solaredge_one.SolarEdgeOneClient") as mock_cls:
        client = mock_cls.return_value
        client.get_site_overview = AsyncMock(return_value=_overview())
        client.get_power = AsyncMock(return_value=_power())
        client.get_alerts = AsyncMock(return_value=alerts or [])
        client.get_devices = AsyncMock(return_value=devices or [])
        client.get_energy = AsyncMock(return_value=_power())
        client.get_sites = AsyncMock(return_value=[])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_alerts_sensor_off_when_no_alerts(hass: HomeAssistant) -> None:
    await _setup(hass, alerts=[])
    state = hass.states.get("binary_sensor.my_home_alerts")
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["alert_count"] == 0


async def test_alerts_sensor_on_and_exposes_payload(hass: HomeAssistant) -> None:
    alerts = [{"id": 1, "severity": "HIGH", "message": "Inverter fault"}]
    await _setup(hass, alerts=alerts)
    state = hass.states.get("binary_sensor.my_home_alerts")
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["alert_count"] == 1
    assert state.attributes["alerts"] == alerts


async def test_credit_sensors_report_ledger(hass: HomeAssistant) -> None:
    """Credit sensors reflect the live ledger (used and budget - used)."""
    entry = await _setup(hass, alerts=[])
    # The mocked client bypasses the real HTTP path, so spend is simulated here.
    coordinator = entry.runtime_data.coordinator
    coordinator.ledger.record(cost=3)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    used = hass.states.get("sensor.my_home_credits_used_this_month")
    remaining = hass.states.get("sensor.my_home_credits_remaining_this_month")
    assert used is not None and remaining is not None
    assert used.state == "3"
    assert remaining.state == "1997"


async def test_inverter_connectivity_and_optimizers(hass: HomeAssistant) -> None:
    inverter = Device(
        type="INVERTER",
        serial_number="INV-123",
        name="Inverter 1",
        active=True,
        connected_optimizers=14,
    )
    await _setup(hass, devices=[inverter])

    online = hass.states.get("binary_sensor.inverter_1_connectivity")
    assert online is not None
    assert online.state == STATE_ON

    optimizers = hass.states.get("sensor.inverter_1_connected_optimizers")
    assert optimizers is not None
    assert optimizers.state == "14"
