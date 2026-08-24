"""Tests for the adaptive, credit-budgeted coordinator (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

from aiosolaredge_one import (
    BudgetPlan,
    ConsumptionOverview,
    CreditLedger,
    ProductionOverview,
    SiteOverview,
    SolarEdgeRateLimitError,
    TimeSeries,
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


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, client: Mock, ledger: CreditLedger
) -> SolarEdgeOneCoordinator:
    return SolarEdgeOneCoordinator(
        hass, entry, client, 3066774, ledger, ledger_store(hass, entry.entry_id)
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
    ledger = CreditLedger(monthly_budget=2000)

    coord = _coordinator(hass, entry, client, ledger)
    await coord.async_refresh()

    assert coord.last_update_success is True
    assert coord.data is not None
    assert MIN_INTERVAL <= coord.update_interval <= MAX_INTERVAL
    # Ledger was written to storage.
    assert await ledger_store(hass, entry.entry_id).async_load() is not None


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
