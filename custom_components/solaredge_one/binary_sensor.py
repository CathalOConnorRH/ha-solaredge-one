"""Binary sensor platform for SolarEdge ONE.

- A site-level *Problem* sensor that is ``on`` while the site has open alerts,
  exposing the raw alert payloads as an attribute.
- A per-inverter *Connectivity* sensor reflecting the inverter's reported
  ``active`` state.
"""

from __future__ import annotations

from typing import Any

from aiosolaredge_one import Device
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SolarEdgeOneConfigEntry
from .coordinator import SolarEdgeOneCoordinator
from .entity import SolarEdgeOneDeviceEntity, SolarEdgeOneEntity

# Read-only integration: all entities read from one coordinator, no writes.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarEdgeOneConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SolarEdge ONE binary sensors from a config entry."""
    coordinator = entry.runtime_data.coordinator

    entities: list[BinarySensorEntity] = [SolarEdgeOneAlertsBinarySensor(coordinator)]
    for device in entry.runtime_data.devices:
        if device.type == "INVERTER" and device.serial_number and device.active is not None:
            entities.append(SolarEdgeOneInverterOnlineBinarySensor(coordinator, device))

    async_add_entities(entities)


class SolarEdgeOneAlertsBinarySensor(SolarEdgeOneEntity, BinarySensorEntity):
    """On when the site has one or more open alerts."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "alerts"

    def __init__(self, coordinator: SolarEdgeOneCoordinator) -> None:
        super().__init__(coordinator, key="alerts")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.alerts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        alerts = self.coordinator.data.alerts
        return {"alert_count": len(alerts), "alerts": alerts}


class SolarEdgeOneInverterOnlineBinarySensor(
    SolarEdgeOneDeviceEntity, BinarySensorEntity
):
    """Connectivity of an inverter (from its reported ``active`` flag)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "inverter_online"

    def __init__(self, coordinator: SolarEdgeOneCoordinator, device: Device) -> None:
        super().__init__(coordinator, device, key="online")

    @property
    def is_on(self) -> bool | None:
        return self.device.active
