"""Adaptive, credit-budgeted data update coordinator for SolarEdge ONE.

Phase 3: a single coordinator that paces itself to the monthly credit budget.
Each cycle it fetches the site overview + power (2 credits), records the spend in
a persisted ledger, then recomputes its next interval from the credits still
available for the rest of the month (see ``aiosolaredge_one.budget``). It slows
down at night, backs off exponentially on HTTP 429, and raises a repair issue if
projected month-end spend would exceed the budget.

Tiered coordinators (fast/slow/daily) arrive with the entities in Phase 4; the
pacing math here is already tier-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from aiosolaredge_one import (
    BudgetPlan,
    CreditLedger,
    SiteOverview,
    SolarEdgeAuthError,
    SolarEdgeError,
    SolarEdgeNotFoundError,
    SolarEdgeOneClient,
    SolarEdgeRateLimitError,
    TimeSeries,
    backoff_interval,
    plan_interval,
    project_month_end_usage,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import sun
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BACKOFF_BASE,
    BACKOFF_CAP,
    CONF_CALLS_PER_MINUTE,
    CONF_MONTHLY_BUDGET,
    CONF_SAFETY_FACTOR,
    CREDITS_PER_CYCLE,
    DEFAULT_CALLS_PER_MINUTE,
    DEFAULT_MONTHLY_BUDGET,
    DEFAULT_SAFETY_FACTOR,
    DOMAIN,
    ISSUE_OVER_BUDGET,
    LOGGER,
    MAX_INTERVAL,
    MIN_INTERVAL,
    NIGHT_FACTOR,
)
from .store import save_ledger

if TYPE_CHECKING:
    # Type-only import to avoid a runtime cycle (__init__ imports this module).
    from . import SolarEdgeOneConfigEntry


@dataclass(slots=True)
class SolarEdgeOneData:
    """Container for the data fetched each update cycle."""

    overview: SiteOverview
    power: TimeSeries
    alerts: list[dict[str, Any]]


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return (start_of_this_month, start_of_next_month) in UTC."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    return start, nxt


class SolarEdgeOneCoordinator(DataUpdateCoordinator[SolarEdgeOneData]):
    """Fetch data for a single SolarEdge site, paced to the credit budget."""

    config_entry: SolarEdgeOneConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SolarEdgeOneConfigEntry,
        client: SolarEdgeOneClient,
        site_id: int,
        ledger: CreditLedger,
        store: Store[dict[str, object]],
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{site_id}",
            update_interval=MIN_INTERVAL,
        )
        self.client = client
        self.site_id = site_id
        self.ledger = ledger
        self._store = store
        self._backoff_attempt = 0

    # -- config -> pacing plan ----------------------------------------------
    def _build_plan(self) -> BudgetPlan:
        """Assemble the pacing plan from config data + options (options win)."""
        entry = self.config_entry

        def _opt(key: str, default: Any) -> Any:
            return entry.options.get(key, entry.data.get(key, default))

        return BudgetPlan(
            monthly_budget=int(_opt(CONF_MONTHLY_BUDGET, DEFAULT_MONTHLY_BUDGET)),
            calls_per_minute=int(_opt(CONF_CALLS_PER_MINUTE, DEFAULT_CALLS_PER_MINUTE)),
            credits_per_cycle=CREDITS_PER_CYCLE,
            safety_factor=float(_opt(CONF_SAFETY_FACTOR, DEFAULT_SAFETY_FACTOR)),
            night_factor=NIGHT_FACTOR,
            min_interval=MIN_INTERVAL.total_seconds(),
            max_interval=MAX_INTERVAL.total_seconds(),
        )

    def _is_night(self) -> bool:
        try:
            return not sun.is_up(self.hass)
        except Exception:  # noqa: BLE001 - sun may be unavailable; treat as day
            return False

    # -- fetch --------------------------------------------------------------
    async def _async_update_data(self) -> SolarEdgeOneData:
        try:
            overview = await self.client.get_site_overview(self.site_id)
            power = await self.client.get_power(self.site_id)
            alerts = await self._fetch_alerts()
        except SolarEdgeAuthError as err:
            raise ConfigEntryAuthFailed("Invalid SolarEdge API credentials") from err
        except SolarEdgeRateLimitError as err:
            self._apply_backoff(err.retry_after)
            raise UpdateFailed(f"Rate limited by SolarEdge: {err}") from err
        except SolarEdgeError as err:
            raise UpdateFailed(f"Error communicating with SolarEdge: {err}") from err

        # Success: clear any backoff, persist the ledger, re-pace, guard budget.
        self._backoff_attempt = 0
        await save_ledger(self._store, self.ledger)
        self._reschedule()
        return SolarEdgeOneData(overview=overview, power=power, alerts=alerts)

    async def _fetch_alerts(self) -> list[dict[str, Any]]:
        """Fetch alerts, tolerating sites/plans that don't expose the endpoint."""
        try:
            return await self.client.get_alerts(self.site_id)
        except SolarEdgeNotFoundError:
            return []

    # -- scheduling ---------------------------------------------------------
    def _apply_backoff(self, retry_after: float | None) -> None:
        self._backoff_attempt += 1
        seconds = backoff_interval(
            self._backoff_attempt,
            base=BACKOFF_BASE.total_seconds(),
            cap=BACKOFF_CAP.total_seconds(),
            retry_after=retry_after,
        )
        self.update_interval = timedelta(seconds=seconds)
        LOGGER.warning(
            "SolarEdge rate limit hit for site %s; backing off %.0fs (attempt %d)",
            self.site_id,
            seconds,
            self._backoff_attempt,
        )

    def _reschedule(self) -> None:
        """Set the next interval from remaining budget + time of day."""
        plan = self._build_plan()
        now = datetime.now(UTC)
        month_start, month_end = _month_bounds(now)
        seconds_until_reset = (month_end - now).total_seconds()
        elapsed = (now - month_start).total_seconds()
        total = (month_end - month_start).total_seconds()

        used = plan.monthly_budget - self.ledger.remaining(now=now)
        interval = plan_interval(
            plan,
            used_this_month=used,
            seconds_until_reset=seconds_until_reset,
            is_night=self._is_night(),
        )
        self.update_interval = timedelta(seconds=interval)

        self._update_budget_issue(plan, used=used, elapsed=elapsed, total=total)

    def _update_budget_issue(
        self, plan: BudgetPlan, *, used: int, elapsed: float, total: float
    ) -> None:
        """Raise/clear a repair issue based on projected month-end spend."""
        projected = project_month_end_usage(
            used_this_month=used, elapsed_seconds=elapsed, total_seconds=total
        )
        issue_id = f"{ISSUE_OVER_BUDGET}_{self.config_entry.entry_id}"
        if projected > plan.monthly_budget:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_OVER_BUDGET,
                translation_placeholders={
                    "projected": str(round(projected)),
                    "budget": str(plan.monthly_budget),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
