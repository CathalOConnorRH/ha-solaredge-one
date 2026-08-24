"""Sensor entity tests for the SolarEdge ONE integration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from aiosolaredge_one import (
    ConsumptionOverview,
    Device,
    ProductionOverview,
    SiteOverview,
    TimeSeries,
    TimeValue,
)
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaredge_one.const import (
    CONF_API_KEY,
    CONF_PLAN_TYPE,
    CONF_SITE_ID,
    DOMAIN,
    PLAN_SITE_OWNER,
)

SITE_ID = 3066774


def _make_entry() -> MockConfigEntry:
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


def _power() -> TimeSeries:
    """A power series ending on a null point (as the live API does)."""
    return TimeSeries(
        period_from=None,
        period_to=None,
        unit="W",
        resolution="QUARTER_HOUR",
        values=[
            TimeValue(timestamp="t0", value=100.0),
            TimeValue(timestamp="t1", value=982.23),
            TimeValue(timestamp="t2", value=None),
        ],
    )


def _empty_energy() -> TimeSeries:
    """An empty /energy series → no lifetime/year/month sensors created."""
    return TimeSeries(
        period_from=None, period_to=None, unit="WH", resolution="YEAR", values=[]
    )


def _pv_only_overview() -> SiteOverview:
    """A PV-only site: production total present, everything else null."""
    return SiteOverview(
        site_id=SITE_ID,
        production=ProductionOverview(total=26290.0, unit="WH"),
        consumption=ConsumptionOverview(),
    )


def _full_overview() -> SiteOverview:
    """A site with a meter + battery: all breakdown fields present."""
    return SiteOverview(
        site_id=SITE_ID,
        production=ProductionOverview(
            total=26290.0,
            unit="WH",
            to_self_consumption=10000.0,
            to_storage=5000.0,
            to_grid=11290.0,
        ),
        consumption=ConsumptionOverview(
            total=15000.0,
            unit="WH",
            from_pv=10000.0,
            from_storage=3000.0,
            from_grid=2000.0,
        ),
    )


async def _setup(
    hass: HomeAssistant,
    overview: SiteOverview,
    *,
    devices: list[Device] | None = None,
) -> MockConfigEntry:
    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch("custom_components.solaredge_one.SolarEdgeOneClient") as mock_cls:
        client = mock_cls.return_value
        client.get_site_overview = AsyncMock(return_value=overview)
        client.get_power = AsyncMock(return_value=_power())
        client.get_alerts = AsyncMock(return_value=[])
        client.get_devices = AsyncMock(return_value=devices or [])
        client.get_energy = AsyncMock(return_value=_empty_energy())
        client.get_sites = AsyncMock(return_value=[])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_core_sensors_created_for_pv_only_site(hass: HomeAssistant) -> None:
    """A PV-only site exposes production + power, but no breakdown sensors."""
    await _setup(hass, _pv_only_overview())

    production = hass.states.get("sensor.my_home_production_today")
    assert production is not None
    assert production.state == "26290.0"
    assert production.attributes["state_class"] == "total_increasing"
    assert production.attributes["device_class"] == "energy"
    assert production.attributes["unit_of_measurement"] == "Wh"

    power = hass.states.get("sensor.my_home_current_power")
    assert power is not None
    # latest non-null point of the series
    assert power.state == "982.23"
    assert power.attributes["state_class"] == "measurement"

    # No meter/battery → breakdown sensors are not created.
    assert hass.states.get("sensor.my_home_imported_from_grid_today") is None
    assert hass.states.get("sensor.my_home_exported_to_grid_today") is None
    assert hass.states.get("sensor.my_home_consumption_today") is None


async def test_breakdown_sensors_created_when_reported(hass: HomeAssistant) -> None:
    """A metered + battery site exposes the grid/storage breakdown sensors."""
    await _setup(hass, _full_overview())

    for object_id, expected in (
        ("sensor.my_home_exported_to_grid_today", "11290.0"),
        ("sensor.my_home_imported_from_grid_today", "2000.0"),
        ("sensor.my_home_consumption_today", "15000.0"),
        ("sensor.my_home_consumption_from_solar_today", "10000.0"),
        ("sensor.my_home_consumption_from_storage_today", "3000.0"),
        ("sensor.my_home_production_to_storage_today", "5000.0"),
        ("sensor.my_home_self_consumption_today", "10000.0"),
    ):
        state = hass.states.get(object_id)
        assert state is not None, object_id
        assert state.state == expected
        assert state.attributes["state_class"] == "total_increasing"


async def test_power_unknown_when_series_all_null(hass: HomeAssistant) -> None:
    """At night (all-null power series) the power sensor is 'unknown', not missing."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    empty_power = TimeSeries(
        period_from=None, period_to=None, unit="W", resolution="QUARTER_HOUR", values=[]
    )
    with patch("custom_components.solaredge_one.SolarEdgeOneClient") as mock_cls:
        client = mock_cls.return_value
        client.get_site_overview = AsyncMock(return_value=_pv_only_overview())
        client.get_power = AsyncMock(return_value=empty_power)
        client.get_alerts = AsyncMock(return_value=[])
        client.get_devices = AsyncMock(return_value=[])
        client.get_energy = AsyncMock(return_value=_empty_energy())
        client.get_sites = AsyncMock(return_value=[])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    power = hass.states.get("sensor.my_home_current_power")
    assert power is not None
    assert power.state == STATE_UNKNOWN


async def test_device_tree_registers_site_and_inverter(hass: HomeAssistant) -> None:
    """The Site device and each inverter are registered with via_device."""
    inverter = Device(
        type="INVERTER",
        serial_number="INV-123",
        manufacturer="SolarEdge",
        part_number="SE5000H-RW000BNN4",
        firmware_version="4.25.13",
        name="Inverter 1",
    )
    entry = await _setup(hass, _pv_only_overview(), devices=[inverter])

    registry = dr.async_get(hass)
    site = registry.async_get_device(identifiers={(DOMAIN, str(SITE_ID))})
    assert site is not None
    assert site.manufacturer == "SolarEdge"

    inv = registry.async_get_device(identifiers={(DOMAIN, "INV-123")})
    assert inv is not None
    assert inv.via_device_id == site.id
    assert inv.model == "SE5000H-RW000BNN4"
    assert inv.sw_version == "4.25.13"
    assert entry.runtime_data.devices == [inverter]


async def test_energy_total_sensors_from_energy_endpoint(hass: HomeAssistant) -> None:
    """Lifetime / this-year / this-month sensors come from /energy.

    The YEAR call yields one bucket per year: their sum is lifetime and the
    current year's bucket is year-to-date. The MONTH call yields month-to-date.
    """
    # Current-year bucket is matched by the coordinator against datetime.now().
    this_year = datetime.now(UTC).year
    yearly = TimeSeries(
        period_from=None,
        period_to=None,
        unit="WH",
        resolution="YEAR",
        values=[
            TimeValue(timestamp=f"{this_year - 2}-01-01T00:00:00Z", value=9000000.0),
            TimeValue(timestamp=f"{this_year - 1}-01-01T00:00:00Z", value=8000000.0),
            TimeValue(timestamp=f"{this_year}-01-01T00:00:00Z", value=3534371.0),
        ],
    )
    monthly = TimeSeries(
        period_from=None,
        period_to=None,
        unit="WH",
        resolution="MONTH",
        values=[TimeValue(timestamp=f"{this_year}-08-01T00:00:00Z", value=527197.0)],
    )

    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch("custom_components.solaredge_one.SolarEdgeOneClient") as mock_cls:
        client = mock_cls.return_value
        client.get_site_overview = AsyncMock(return_value=_pv_only_overview())
        client.get_power = AsyncMock(return_value=_power())
        client.get_alerts = AsyncMock(return_value=[])
        client.get_devices = AsyncMock(return_value=[])
        client.get_energy = AsyncMock(side_effect=[yearly, monthly])
        client.get_sites = AsyncMock(return_value=[])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    lifetime = hass.states.get("sensor.my_home_lifetime_production")
    assert lifetime is not None
    assert lifetime.state == "20534371.0"  # 9M + 8M + 3.534M
    assert lifetime.attributes["state_class"] == "total_increasing"
    assert lifetime.attributes["device_class"] == "energy"

    year = hass.states.get("sensor.my_home_production_this_year")
    assert year is not None
    assert year.state == "3534371.0"  # 2026 bucket

    month = hass.states.get("sensor.my_home_production_this_month")
    assert month is not None
    assert month.state == "527197.0"
