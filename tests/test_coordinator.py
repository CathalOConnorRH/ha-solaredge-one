"""Tests for the adaptive, credit-budgeted coordinator (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

from aiosolaredge_one import (
    BudgetPlan,
    ConsumptionOverview,
    CreditLedger,
    Device,
    EnvironmentalBenefits,
    ProductionOverview,
    Site,
    SiteOverview,
    SolarEdgeNotFoundError,
    SolarEdgeRateLimitError,
    TimeSeries,
    TimeValue,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaredge_one.const import (
    CONF_API_KEY,
    CONF_PLAN_TYPE,
    CONF_SITE_ID,
    DOMAIN,
    ISSUE_OVER_BUDGET,
    MAX_INTERVAL,
    MIN_INTERVAL,
    PLAN_SITE_OWNER,
)
from custom_components.solaredge_one.coordinator import SolarEdgeOneCoordinator
from custom_components.solaredge_one.store import (
    ledger_store,
    load_ledger,
    save_ledger,
)

MONTH_SECONDS = 30 * 24 * 3600.0


def _entry() -> MockConfigEntry:
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


def _empty_ts() -> TimeSeries:
    return TimeSeries(period_from=None, period_to=None, unit="WH", resolution=None)


def _coordinator(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: Mock,
    ledger: CreditLedger,
    *,
    install_date: str | None = None,
    has_battery: bool = False,
) -> SolarEdgeOneCoordinator:
    return SolarEdgeOneCoordinator(
        hass,
        entry,
        client,
        3066774,
        ledger,
        ledger_store(hass, entry.entry_id),
        install_date=install_date,
        has_battery=has_battery,
    )


async def test_ledger_round_trip_survives_restart(hass: HomeAssistant) -> None:
    """save_ledger + load_ledger preserve usage across a simulated restart."""
    store = ledger_store(hass, "entry123")
    await save_ledger(store, CreditLedger(monthly_budget=2000, used=57, month="2026-08"))

    restored = await load_ledger(store, monthly_budget=2000)
    assert restored.used == 57
    assert restored.month == "2026-08"
    # Budget always comes from (possibly changed) config, not disk.
    assert restored.monthly_budget == 2000


async def test_successful_cycle_paces_and_persists(hass: HomeAssistant) -> None:
    """A good fetch sets a bounded adaptive interval and persists the ledger."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = Mock()
    client.get_site_overview = AsyncMock(return_value=_overview())
    client.get_power = AsyncMock(return_value=_power())
    client.get_alerts = AsyncMock(return_value=[])
    client.get_lifetime_energy = AsyncMock(return_value=_empty_ts())
    client.get_energy = AsyncMock(return_value=_empty_ts())
    client.get_environmental_benefits = AsyncMock(return_value=EnvironmentalBenefits())
    client.get_sites = AsyncMock(return_value=[])
    ledger = CreditLedger(monthly_budget=2000)

    coord = _coordinator(hass, entry, client, ledger)
    await coord.async_refresh()

    assert coord.last_update_success is True
    assert coord.data is not None
    assert MIN_INTERVAL <= coord.update_interval <= MAX_INTERVAL
    # Ledger was written to storage.
    assert await ledger_store(hass, entry.entry_id).async_load() is not None


def _month_buckets(now: datetime) -> tuple[TimeSeries, float, float]:
    """Build a Jan..current-month MONTH series + its (ytd sum, mtd) totals."""
    values = [
        TimeValue(
            timestamp=f"{now.year}-{m:02d}-01T00:00:00Z", value=100000.0 * m
        )
        for m in range(1, now.month + 1)
    ]
    series = TimeSeries(
        period_from=None, period_to=None, unit="WH", resolution="MONTH", values=values
    )
    ytd = sum(100000.0 * m for m in range(1, now.month + 1))
    mtd = 100000.0 * now.month
    return series, ytd, mtd


async def test_energy_totals_use_install_date_and_populate(
    hass: HomeAssistant,
) -> None:
    """A clean cycle uses TOTAL for lifetime and MONTH for this-year/this-month."""
    entry = _entry()
    entry.add_to_hass(hass)
    now = datetime.now(UTC)
    total = TimeSeries(
        period_from=None,
        period_to=None,
        unit="WH",
        resolution="TOTAL",
        values=[TimeValue(timestamp=f"{now.year}-01-01T00:00:00Z", value=11500000.0)],
    )
    monthly, ytd, mtd = _month_buckets(now)
    client = Mock()
    client.get_site_overview = AsyncMock(return_value=_overview())
    client.get_power = AsyncMock(return_value=_power())
    client.get_alerts = AsyncMock(return_value=[])
    client.get_lifetime_energy = AsyncMock(return_value=total)
    client.get_energy = AsyncMock(return_value=monthly)
    client.get_environmental_benefits = AsyncMock(return_value=EnvironmentalBenefits())
    client.get_sites = AsyncMock(
        return_value=[
            Site(site_id=3066774, name="My Home", installation_date="2019-06-01")
        ]
    )

    coord = _coordinator(hass, entry, client, CreditLedger(monthly_budget=2000))
    await coord.async_refresh()

    assert coord.last_update_success is True
    assert coord.data.energy.lifetime == 11500000.0
    assert coord.data.energy.year_to_date == ytd
    assert coord.data.energy.month_to_date == mtd
    # Lifetime came from the TOTAL call (no YEAR fallback needed).
    client.get_lifetime_energy.assert_awaited_once()
    # The install date bounded the lifetime window (from=2019-06-01).
    assert client.get_lifetime_energy.await_args.kwargs["date_from"].year == 2019
    # Only the MONTH call goes through get_energy when TOTAL succeeds.
    assert client.get_energy.await_count == 1
    assert client.get_energy.await_args.kwargs["resolution"] == "MONTH"


async def test_lifetime_falls_back_to_year_when_total_empty(
    hass: HomeAssistant,
) -> None:
    """An empty TOTAL response falls back to summing a YEAR call for lifetime."""
    entry = _entry()
    entry.add_to_hass(hass)
    now = datetime.now(UTC)
    yearly = TimeSeries(
        period_from=None,
        period_to=None,
        unit="WH",
        resolution="YEAR",
        values=[
            TimeValue(timestamp=f"{now.year - 1}-01-01T00:00:00Z", value=8000000.0),
            TimeValue(timestamp=f"{now.year}-01-01T00:00:00Z", value=3500000.0),
        ],
    )
    monthly, ytd, mtd = _month_buckets(now)
    client = Mock()
    client.get_site_overview = AsyncMock(return_value=_overview())
    client.get_power = AsyncMock(return_value=_power())
    client.get_alerts = AsyncMock(return_value=[])
    client.get_lifetime_energy = AsyncMock(return_value=_empty_ts())
    client.get_energy = AsyncMock(side_effect=[yearly, monthly])
    client.get_environmental_benefits = AsyncMock(return_value=EnvironmentalBenefits())
    client.get_sites = AsyncMock(return_value=[])

    coord = _coordinator(hass, entry, client, CreditLedger(monthly_budget=2000))
    await coord.async_refresh()

    assert coord.last_update_success is True
    assert coord.data.energy.lifetime == 11500000.0  # 8M + 3.5M from the YEAR call
    assert coord.data.energy.year_to_date == ytd
    assert coord.data.energy.month_to_date == mtd
    # First get_energy call is the YEAR fallback, second is the MONTH call.
    assert client.get_energy.await_args_list[0].kwargs["resolution"] == "YEAR"
    assert client.get_energy.await_args_list[1].kwargs["resolution"] == "MONTH"


async def test_energy_not_found_is_tolerated(hass: HomeAssistant) -> None:
    """A site/plan without /energy leaves totals empty but the cycle succeeds."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = Mock()
    client.get_site_overview = AsyncMock(return_value=_overview())
    client.get_power = AsyncMock(return_value=_power())
    client.get_alerts = AsyncMock(return_value=[])
    client.get_lifetime_energy = AsyncMock(
        side_effect=SolarEdgeNotFoundError("no energy")
    )
    client.get_energy = AsyncMock(side_effect=SolarEdgeNotFoundError("no energy"))
    client.get_environmental_benefits = AsyncMock(return_value=EnvironmentalBenefits())
    client.get_sites = AsyncMock(return_value=[])

    coord = _coordinator(hass, entry, client, CreditLedger(monthly_budget=2000))
    await coord.async_refresh()

    assert coord.last_update_success is True
    assert coord.data.energy.lifetime is None
    assert coord.data.energy.year_to_date is None
    assert coord.data.energy.month_to_date is None


async def test_environmental_benefits_populate(hass: HomeAssistant) -> None:
    """A clean cycle fills the environmental-benefits snapshot."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = Mock()
    client.get_site_overview = AsyncMock(return_value=_overview())
    client.get_power = AsyncMock(return_value=_power())
    client.get_alerts = AsyncMock(return_value=[])
    client.get_lifetime_energy = AsyncMock(return_value=_empty_ts())
    client.get_energy = AsyncMock(return_value=_empty_ts())
    client.get_environmental_benefits = AsyncMock(
        return_value=EnvironmentalBenefits(co2_emissions=1234.5, ev_miles=678.0)
    )
    client.get_sites = AsyncMock(return_value=[])

    coord = _coordinator(hass, entry, client, CreditLedger(monthly_budget=2000))
    await coord.async_refresh()

    assert coord.data.environmental.co2_emissions == 1234.5
    assert coord.data.environmental.ev_miles == 678.0
    assert client.get_environmental_benefits.await_args.kwargs["unit"] == "METRIC"


async def test_storage_skipped_without_battery(hass: HomeAssistant) -> None:
    """PV-only sites never call the storage endpoint and get an empty snapshot."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = Mock()
    client.get_site_overview = AsyncMock(return_value=_overview())
    client.get_power = AsyncMock(return_value=_power())
    client.get_alerts = AsyncMock(return_value=[])
    client.get_lifetime_energy = AsyncMock(return_value=_empty_ts())
    client.get_energy = AsyncMock(return_value=_empty_ts())
    client.get_environmental_benefits = AsyncMock(return_value=EnvironmentalBenefits())
    client.get_storage_telemetry = AsyncMock(return_value={})
    client.get_sites = AsyncMock(return_value=[])

    coord = _coordinator(hass, entry, client, CreditLedger(monthly_budget=2000))
    await coord.async_refresh()

    client.get_storage_telemetry.assert_not_called()
    assert coord.data.storage.state_of_charge is None


async def test_storage_telemetry_parsed_when_battery_present(
    hass: HomeAssistant,
) -> None:
    """With a battery, the raw telemetry is fetched and parsed defensively."""
    entry = _entry()
    entry.add_to_hass(hass)
    raw = {
        "telemetries": [
            {"timestamp": "2026-08-26T10:00:00Z", "stateOfEnergy": 40.0},
            {"timestamp": "2026-08-26T10:15:00Z", "stateOfEnergy": 55.5},
        ],
        "dischargePower": 1200.0,
        "batteryRemainingEnergy": 4800.0,
    }
    client = Mock()
    client.get_site_overview = AsyncMock(return_value=_overview())
    client.get_power = AsyncMock(return_value=_power())
    client.get_alerts = AsyncMock(return_value=[])
    client.get_lifetime_energy = AsyncMock(return_value=_empty_ts())
    client.get_energy = AsyncMock(return_value=_empty_ts())
    client.get_environmental_benefits = AsyncMock(return_value=EnvironmentalBenefits())
    client.get_storage_telemetry = AsyncMock(return_value=raw)
    client.get_sites = AsyncMock(return_value=[])

    coord = _coordinator(
        hass, entry, client, CreditLedger(monthly_budget=2000), has_battery=True
    )
    await coord.async_refresh()

    client.get_storage_telemetry.assert_awaited_once()
    assert coord.data.storage.state_of_charge == 55.5  # latest point in the series
    assert coord.data.storage.discharge_power == 1200.0
    assert coord.data.storage.remaining_energy == 4800.0
    assert coord.data.storage.charge_power is None  # not present in payload


def test_has_battery_detection() -> None:
    """_has_battery recognises battery/storage device types."""
    from custom_components.solaredge_one import _has_battery

    assert _has_battery([Device(type="BATTERY", serial_number="B1")]) is True
    assert _has_battery([Device(type="STORAGE", serial_number="S1")]) is True
    assert _has_battery([Device(type="INVERTER", serial_number="I1")]) is False
    assert _has_battery([]) is False


def test_parse_storage_state_handles_empty() -> None:
    """An empty/unrecognised payload yields an all-None snapshot."""
    from custom_components.solaredge_one.coordinator import parse_storage_state

    state = parse_storage_state({})
    assert state.state_of_charge is None
    assert state.charge_power is None
    assert state.discharge_power is None
    assert state.remaining_energy is None


async def test_rate_limit_backs_off_without_crashing(hass: HomeAssistant) -> None:
    """A 429 marks the update failed and applies exponential backoff."""
    entry = _entry()
    entry.add_to_hass(hass)
    client = Mock()
    client.get_site_overview = AsyncMock(
        side_effect=SolarEdgeRateLimitError("rate", retry_after=None)
    )
    client.get_power = AsyncMock(return_value=_power())
    ledger = CreditLedger(monthly_budget=2000)

    coord = _coordinator(hass, entry, client, ledger)

    await coord.async_refresh()
    assert coord.last_update_success is False
    assert coord.update_interval == timedelta(minutes=1)  # first backoff

    await coord.async_refresh()
    assert coord.last_update_success is False
    assert coord.update_interval == timedelta(minutes=2)  # doubles


async def test_rate_limit_honours_retry_after(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    client = Mock()
    client.get_site_overview = AsyncMock(
        side_effect=SolarEdgeRateLimitError("rate", retry_after=300)
    )
    coord = _coordinator(hass, entry, client, CreditLedger(monthly_budget=2000))

    await coord.async_refresh()
    assert coord.update_interval == timedelta(seconds=300)


async def test_budget_guard_raises_and_clears_issue(hass: HomeAssistant) -> None:
    """The projection guard creates a repair issue and later clears it."""
    entry = _entry()
    entry.add_to_hass(hass)
    coord = _coordinator(hass, entry, Mock(), CreditLedger(monthly_budget=2000))
    plan = BudgetPlan(monthly_budget=2000)
    issue_id = f"{ISSUE_OVER_BUDGET}_{entry.entry_id}"
    registry = ir.async_get(hass)

    # Spent 1000 in the first day → projects to ~30000, far over budget.
    coord._update_budget_issue(
        plan, used=1000, elapsed=24 * 3600, total=MONTH_SECONDS
    )
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    # Spent only 10 with 90% of the month elapsed → projects ~11, under budget.
    coord._update_budget_issue(
        plan, used=10, elapsed=0.9 * MONTH_SECONDS, total=MONTH_SECONDS
    )
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_night_interval_is_longer_than_day(hass: HomeAssistant) -> None:
    """Night slowdown yields a longer interval than daytime for the same state."""
    entry = _entry()
    entry.add_to_hass(hass)
    coord = _coordinator(hass, entry, Mock(), CreditLedger(monthly_budget=2000))
    plan = coord._build_plan()
    now = datetime.now(UTC)
    seconds_left = MONTH_SECONDS

    from aiosolaredge_one import plan_interval

    day = plan_interval(
        plan, used_this_month=0, seconds_until_reset=seconds_left, is_night=False
    )
    night = plan_interval(
        plan, used_this_month=0, seconds_until_reset=seconds_left, is_night=True
    )
    assert night > day
    assert now.tzinfo is UTC  # sanity: helper uses aware UTC time
