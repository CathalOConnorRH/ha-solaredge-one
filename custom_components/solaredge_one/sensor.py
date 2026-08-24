"""Sensor platform for SolarEdge ONE.

Most energy sensors are today's running totals (Wh) from ``/overview`` — they
climb through the day and reset at midnight, so they are exposed as
``total_increasing`` (which handles the daily reset) and feed the Home Assistant
Energy Dashboard directly. The lifetime / this-year / this-month production
totals come from a separate, throttled ``/energy`` fetch (see the coordinator)
and are only created when the site/plan actually reports them. Current power is a
``measurement`` in W taken from the most recent non-null point of the ``/power``
time series.

Consumption / grid / storage breakdown sensors only exist when the site actually
reports them — a PV-only site (no meter, no battery) returns ``null`` for those
fields, so the corresponding entities are simply not created.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aiosolaredge_one import Device
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SolarEdgeOneConfigEntry
from .coordinator import SolarEdgeOneCoordinator, SolarEdgeOneData
from .entity import SolarEdgeOneDeviceEntity, SolarEdgeOneEntity


@dataclass(frozen=True, kw_only=True)
class SolarEdgeOneSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor and an always-create flag."""

    value_fn: Callable[[SolarEdgeOneData], float | None]
    # Core sensors are created even when momentarily null (e.g. power at night);
    # the rest are only created when the site reports a non-null value at setup.
    always: bool = False


def _energy(
    key: str,
    value_fn: Callable[[SolarEdgeOneData], float | None],
    *,
    always: bool = False,
) -> SolarEdgeOneSensorDescription:
    """A cumulative-Wh energy sensor (feeds the Energy Dashboard)."""
    return SolarEdgeOneSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        value_fn=value_fn,
        always=always,
    )


SENSORS: tuple[SolarEdgeOneSensorDescription, ...] = (
    _energy(
        "production_today",
        lambda data: data.overview.production.total,
        always=True,
    ),
    # Slow cumulative totals from /energy (only created when available).
    _energy("lifetime_production", lambda data: data.energy.lifetime),
    _energy("production_this_year", lambda data: data.energy.year_to_date),
    _energy("production_this_month", lambda data: data.energy.month_to_date),
    SolarEdgeOneSensorDescription(
        key="current_power",
        translation_key="current_power",
        always=True,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda data: data.power.latest_value,
    ),
    _energy("production_to_grid", lambda data: data.overview.production.to_grid),
    _energy(
        "production_self_consumption",
        lambda data: data.overview.production.to_self_consumption,
    ),
    _energy("production_to_storage", lambda data: data.overview.production.to_storage),
    _energy("consumption_total", lambda data: data.overview.consumption.total),
    _energy("consumption_from_grid", lambda data: data.overview.consumption.from_grid),
    _energy("consumption_from_pv", lambda data: data.overview.consumption.from_pv),
    _energy(
        "consumption_from_storage",
        lambda data: data.overview.consumption.from_storage,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarEdgeOneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SolarEdge ONE sensors from a config entry."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data

    entities: list[SensorEntity] = [
        SolarEdgeOneSensor(coordinator, description)
        for description in SENSORS
        if description.always or description.value_fn(data) is not None
    ]

    # Diagnostic: local credit-budget accounting (no extra API call).
    entities.append(
        SolarEdgeOneCreditSensor(
            coordinator,
            key="credits_used",
            value_fn=lambda coord: coord.ledger.used,
        )
    )
    entities.append(
        SolarEdgeOneCreditSensor(
            coordinator,
            key="credits_remaining",
            value_fn=lambda coord: coord.ledger.remaining(),
        )
    )

    # Per-inverter inventory diagnostics (from the one-off /devices fetch).
    for device in entry.runtime_data.devices:
        if (
            device.type == "INVERTER"
            and device.serial_number
            and device.connected_optimizers is not None
        ):
            entities.append(SolarEdgeOneOptimizerCountSensor(coordinator, device))

    async_add_entities(entities)


class SolarEdgeOneSensor(SolarEdgeOneEntity, SensorEntity):
    """A single SolarEdge ONE site-level sensor."""

    entity_description: SolarEdgeOneSensorDescription

    def __init__(
        self,
        coordinator: SolarEdgeOneCoordinator,
        description: SolarEdgeOneSensorDescription,
    ) -> None:
        super().__init__(coordinator, key=description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data)


class SolarEdgeOneCreditSensor(SolarEdgeOneEntity, SensorEntity):
    """A diagnostic sensor reporting local credit-ledger figures."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "credits"

    def __init__(
        self,
        coordinator: SolarEdgeOneCoordinator,
        *,
        key: str,
        value_fn: Callable[[SolarEdgeOneCoordinator], int],
    ) -> None:
        super().__init__(coordinator, key=key)
        self._attr_translation_key = key
        self._value_fn = value_fn

    @property
    def native_value(self) -> int:
        return self._value_fn(self.coordinator)


class SolarEdgeOneOptimizerCountSensor(SolarEdgeOneDeviceEntity, SensorEntity):
    """Number of optimizers connected to an inverter (diagnostic)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "connected_optimizers"

    def __init__(self, coordinator: SolarEdgeOneCoordinator, device: Device) -> None:
        super().__init__(coordinator, device, key="connected_optimizers")

    @property
    def native_value(self) -> int | None:
        return self.device.connected_optimizers
