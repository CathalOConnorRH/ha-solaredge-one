"""Setup/unload tests for the SolarEdge ONE integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from aiosolaredge_one import (
    ConsumptionOverview,
    ProductionOverview,
    SiteOverview,
    TimeSeries,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaredge_one.const import (
    CONF_API_KEY,
    CONF_PLAN_TYPE,
    CONF_SITE_ID,
    DOMAIN,
    PLAN_SITE_OWNER,
)


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="3066774",
        title="My Home",
        data={
            CONF_PLAN_TYPE: PLAN_SITE_OWNER,
            CONF_API_KEY: "test-key",
            CONF_SITE_ID: 3066774,
        },
    )


def _overview() -> SiteOverview:
    return SiteOverview(
        site_id=3066774,
        production=ProductionOverview(total=26290.0, unit="WH"),
        consumption=ConsumptionOverview(),
    )


def _power() -> TimeSeries:
    return TimeSeries(
        period_from=None, period_to=None, unit="W", resolution="QUARTER_HOUR", values=[]
    )


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch("custom_components.solaredge_one.SolarEdgeOneClient") as mock_cls:
        client = mock_cls.return_value
        client.get_site_overview = AsyncMock(return_value=_overview())
        client.get_power = AsyncMock(return_value=_power())
        client.get_alerts = AsyncMock(return_value=[])
        client.get_devices = AsyncMock(return_value=[])

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.coordinator.data.overview.production.total == 26290.0

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_auth_failure_triggers_reauth(hass: HomeAssistant) -> None:
    """An auth error during first refresh sends the entry to SETUP_ERROR."""
    from aiosolaredge_one import SolarEdgeAuthError

    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch("custom_components.solaredge_one.SolarEdgeOneClient") as mock_cls:
        client = mock_cls.return_value
        client.get_site_overview = AsyncMock(side_effect=SolarEdgeAuthError("bad"))
        client.get_power = AsyncMock(return_value=_power())

        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
