"""Adaptive, credit-budgeted data update coordinator for SolarEdge ONE.

A single coordinator that paces itself to the monthly credit budget. Each cycle
it fetches the site overview + power + alerts (3 credits), records the spend in a
persisted ledger, then recomputes its next interval from the credits still
available for the rest of the month (see ``aiosolaredge_one.budget``). It slows
down at night, backs off exponentially on HTTP 429, and raises a repair issue if
projected month-end spend would exceed the budget.

The slow lifetime / year-to-date / month-to-date energy totals come from a
separate ``/energy`` fetch, throttled to ``ENERGY_REFRESH_INTERVAL`` and skipped
at night, and cached between polls so they add only a couple of credits per hour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from aiosolaredge_one import (
    BudgetPlan,
    CreditLedger,
    EnvironmentalBenefits,
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
    ENERGY_REFRESH_INTERVAL,
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
class EnergyTotals:
    """Slow-moving cumulative energy figures derived from ``/energy``.

    All in Wh. ``None`` means "not (yet) available" — e.g. a plan/site that does
    not expose ``/energy``, or before the first refresh.
    """

    lifetime: float | None = None
    year_to_date: float | None = None
    month_to_date: float | None = None


@dataclass(slots=True)
class StorageState:
    """Battery telemetry snapshot derived from ``/storage/telemetry``.

    All fields default to ``None`` — the live v2 storage payload shape has not
    been captured yet, so parsing is best-effort (see ``parse_storage_state``)
    and a PV-only or unrecognised payload simply yields an empty snapshot. Powers
    are in W, energies in Wh, state of charge in %.
    """

    state_of_charge: float | None = None
    charge_power: float | None = None
    discharge_power: float | None = None
    remaining_energy: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SolarEdgeOneData:
    """Container for the data fetched each update cycle."""

    overview: SiteOverview
    power: TimeSeries
    alerts: list[dict[str, Any]]
    energy: EnergyTotals = field(default_factory=EnergyTotals)
    environmental: EnvironmentalBenefits = field(default_factory=EnvironmentalBenefits)
    storage: StorageState = field(default_factory=StorageState)


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return (start_of_this_month, start_of_next_month) in UTC."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    return start, nxt


def _as_float(value: Any) -> float | None:
    """Coerce a scalar (or a {timestamp,value} point) to float, else ``None``."""
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _deep_find_latest(obj: Any, keys: tuple[str, ...]) -> float | None:
    """Best-effort search of a nested payload for the latest value of a metric.

    The live v2 storage telemetry shape is uncaptured, so rather than assume a
    layout we walk the whole structure looking for any of ``keys`` (case-
    insensitive). Both shapes are handled: a metric held directly (as a scalar or
    a ``{timestamp, value}`` series), and a metric that is a key inside each point
    of a telemetry list. Payloads are assumed chronological, so the last match
    encountered wins. Returns ``None`` when nothing matches — an unrecognised
    payload then yields no sensors.
    """
    wanted = {k.lower() for k in keys}
    latest: float | None = None

    def _consider(value: Any) -> None:
        nonlocal latest
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            found = _as_float(candidate)
            if found is not None:
                latest = found

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in wanted:
                    _consider(value)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(obj)
    return latest


def parse_storage_state(raw: dict[str, Any]) -> StorageState:
    """Best-effort extraction of the battery metrics from raw storage telemetry.

    Key aliases cover both the v1 field names and the likely v2 renames; the true
    v2 shape is captured in diagnostics for later refinement.
    """
    return StorageState(
        state_of_charge=_deep_find_latest(
            raw, ("stateOfEnergy", "batteryPercentageState", "stateOfCharge", "soc")
        ),
        charge_power=_deep_find_latest(raw, ("chargePower", "charging", "powerCharge")),
        discharge_power=_deep_find_latest(
            raw, ("dischargePower", "discharging", "powerDischarge")
        ),
        remaining_energy=_deep_find_latest(
            raw, ("remainingEnergy", "availableEnergy", "batteryRemainingEnergy")
        ),
        raw=raw,
    )


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
        *,
        install_date: str | None = None,
        has_battery: bool = False,
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
        self._has_battery = has_battery
        # Slow-moving energy totals: cached between polls, refreshed on a throttle.
        self._energy: EnergyTotals | None = None
        self._energy_fetched_at: datetime | None = None
        # ``None`` = not yet resolved (triggers a one-off /sites lookup); ``""`` =
        # resolved-but-unknown. Seeded from /sites/{id} details at setup.
        self._install_date: str | None = install_date
        # TOTAL-resolution lifetime is unverified against the live site; once it
        # returns nothing we stop relying on it and use the YEAR fallback.
        self._total_unsupported = False
        # Slow-moving environmental benefits: same throttle as the energy totals.
        self._environmental: EnvironmentalBenefits | None = None
        self._environmental_fetched_at: datetime | None = None

    # -- config -> pacing plan ----------------------------------------------
    def _build_plan(self) -> BudgetPlan:
        """Assemble the pacing plan from config data + options (options win)."""
        entry = self.config_entry

        def _opt(key: str, default: Any) -> Any:
            return entry.options.get(key, entry.data.get(key, default))

        # A battery adds a storage-telemetry call to every cycle; count it so the
        # pacing projection stays honest for battery sites.
        credits_per_cycle = CREDITS_PER_CYCLE + (1 if self._has_battery else 0)
        return BudgetPlan(
            monthly_budget=int(_opt(CONF_MONTHLY_BUDGET, DEFAULT_MONTHLY_BUDGET)),
            calls_per_minute=int(_opt(CONF_CALLS_PER_MINUTE, DEFAULT_CALLS_PER_MINUTE)),
            credits_per_cycle=credits_per_cycle,
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
        now = datetime.now(UTC)
        try:
            overview = await self.client.get_site_overview(self.site_id)
            power = await self.client.get_power(self.site_id)
            alerts = await self._fetch_alerts()
            energy = await self._maybe_fetch_energy(now)
            environmental = await self._maybe_fetch_environmental(now)
            storage = await self._fetch_storage()
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
        return SolarEdgeOneData(
            overview=overview,
            power=power,
            alerts=alerts,
            energy=energy,
            environmental=environmental,
            storage=storage,
        )

    async def _fetch_alerts(self) -> list[dict[str, Any]]:
        """Fetch alerts, tolerating sites/plans that don't expose the endpoint."""
        try:
            return await self.client.get_alerts(self.site_id)
        except SolarEdgeNotFoundError:
            return []

    # -- slow energy totals (lifetime / this-year / this-month) -------------
    async def _maybe_fetch_energy(self, now: datetime) -> EnergyTotals:
        """Refresh the slow energy totals on a throttle; else reuse the cache.

        These come from ``/energy`` (separate from the per-cycle overview) and
        move slowly, so we fetch at most every ``ENERGY_REFRESH_INTERVAL`` and
        skip at night. Rate-limit / auth errors propagate (so the normal backoff
        and reauth paths apply); a missing endpoint or transient error just
        leaves the last known values in place.
        """
        due = self._energy is None or (
            self._energy_fetched_at is not None
            and (now - self._energy_fetched_at) >= ENERGY_REFRESH_INTERVAL
            and not self._is_night()
        )
        if due:
            try:
                await self._refresh_energy(now)
            except SolarEdgeNotFoundError:
                # Plan/site does not expose /energy — stop trying, no sensors.
                self._energy = EnergyTotals()
                self._energy_fetched_at = now
            except (SolarEdgeAuthError, SolarEdgeRateLimitError):
                raise
            except SolarEdgeError as err:
                LOGGER.debug(
                    "Energy totals refresh failed for site %s: %s", self.site_id, err
                )
        return self._energy or EnergyTotals()

    async def _refresh_energy(self, now: datetime) -> None:
        """Refresh lifetime + year-to-date + month-to-date from ``/energy``.

        Two calls in the common case: a ``resolution=TOTAL`` call (the v2 way to
        get a single lifetime bucket) plus a MONTH call spanning the current year
        whose sum is year-to-date and whose current-month bucket is
        month-to-date. TOTAL is unverified against the live site, so if it yields
        nothing we fall back to summing a YEAR call and stop calling TOTAL.
        """
        await self._ensure_install_date()
        start = self._lifetime_window_start(now)

        lifetime = await self._fetch_lifetime(start, now)

        # MONTH resolution across this year: sum = year-to-date, the current
        # month's bucket = month-to-date.
        year_start = now.replace(
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        monthly = await self.client.get_energy(
            self.site_id, date_from=year_start, date_to=now, resolution="MONTH"
        )
        year_to_date = monthly.total if monthly.non_null_values else None
        month_to_date = self._bucket_prefix(monthly, now.strftime("%Y-%m"))

        self._energy = EnergyTotals(
            lifetime=lifetime,
            year_to_date=year_to_date,
            month_to_date=month_to_date,
        )
        self._energy_fetched_at = now

    async def _fetch_lifetime(self, start: datetime, now: datetime) -> float | None:
        """Lifetime Wh via ``resolution=TOTAL``, falling back to a YEAR sum."""
        if not self._total_unsupported:
            try:
                total = await self.client.get_lifetime_energy(
                    self.site_id, date_from=start, date_to=now
                )
            except SolarEdgeNotFoundError:
                self._total_unsupported = True
            else:
                if total.non_null_values:
                    return total.total
                # Empty payload: TOTAL isn't giving us anything useful here.
                self._total_unsupported = True

        yearly = await self.client.get_energy(
            self.site_id, date_from=start, date_to=now, resolution="YEAR"
        )
        return yearly.total if yearly.non_null_values else None

    async def _ensure_install_date(self) -> None:
        """Look up the site's installation date once (for the lifetime window)."""
        if self._install_date is not None:
            return
        self._install_date = ""  # mark resolved even if we can't find it
        for site in await self.client.get_sites():
            if site.site_id == self.site_id:
                self._install_date = site.installation_date or ""
                return

    def _lifetime_window_start(self, now: datetime) -> datetime:
        try:
            return datetime.strptime(
                (self._install_date or "")[:10], "%Y-%m-%d"
            ).replace(tzinfo=UTC)
        except ValueError:
            # Unknown install date: look back far enough to cover any real system.
            return datetime(now.year - 20, 1, 1, tzinfo=UTC)

    @staticmethod
    def _bucket_prefix(series: TimeSeries, prefix: str) -> float | None:
        """Value of the first non-null bucket whose timestamp starts with ``prefix``."""
        for value in series.values:
            if value.value is not None and value.timestamp.startswith(prefix):
                return value.value
        return None

    # -- environmental benefits (CO2 saved / EV miles) ----------------------
    async def _maybe_fetch_environmental(
        self, now: datetime
    ) -> EnvironmentalBenefits:
        """Refresh environmental benefits on the energy throttle; else reuse cache."""
        due = self._environmental is None or (
            self._environmental_fetched_at is not None
            and (now - self._environmental_fetched_at) >= ENERGY_REFRESH_INTERVAL
            and not self._is_night()
        )
        if due:
            try:
                self._environmental = await self.client.get_environmental_benefits(
                    self.site_id, unit="METRIC"
                )
                self._environmental_fetched_at = now
            except SolarEdgeNotFoundError:
                self._environmental = EnvironmentalBenefits()
                self._environmental_fetched_at = now
            except (SolarEdgeAuthError, SolarEdgeRateLimitError):
                raise
            except SolarEdgeError as err:
                LOGGER.debug(
                    "Environmental benefits refresh failed for site %s: %s",
                    self.site_id,
                    err,
                )
        return self._environmental or EnvironmentalBenefits()

    # -- battery telemetry --------------------------------------------------
    async def _fetch_storage(self) -> StorageState:
        """Fetch battery telemetry when the site has a battery; else empty.

        Fetched every cycle (it changes minute-to-minute, unlike the energy
        totals). Gated on battery presence so PV-only sites never spend a credit
        here, and tolerant of a missing endpoint / unrecognised payload.
        """
        if not self._has_battery:
            return StorageState()
        try:
            raw = await self.client.get_storage_telemetry(self.site_id)
        except SolarEdgeNotFoundError:
            return StorageState()
        except (SolarEdgeAuthError, SolarEdgeRateLimitError):
            raise
        except SolarEdgeError as err:
            LOGGER.debug(
                "Storage telemetry fetch failed for site %s: %s", self.site_id, err
            )
            return StorageState()
        return parse_storage_state(raw)

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
