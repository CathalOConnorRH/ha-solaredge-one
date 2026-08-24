"""Base entities for the SolarEdge ONE integration."""

from __future__ import annotations

from aiosolaredge_one import Device
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolarEdgeOneCoordinator


class SolarEdgeOneEntity(CoordinatorEntity[SolarEdgeOneCoordinator]):
    """Common base: shares the coordinator and the Site device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SolarEdgeOneCoordinator, *, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.site_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.site_id))},
            manufacturer="SolarEdge",
            model="SolarEdge ONE Site",
            name=coordinator.config_entry.title,
            configuration_url="https://monitoring.solaredge.com",
        )


class SolarEdgeOneDeviceEntity(CoordinatorEntity[SolarEdgeOneCoordinator]):
    """Base for entities that belong to a child device (e.g. an inverter)."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SolarEdgeOneCoordinator, device: Device, *, key: str
    ) -> None:
        super().__init__(coordinator)
        self.device = device
        serial = device.serial_number or ""
        self._attr_unique_id = f"{coordinator.site_id}_{serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer=device.manufacturer or "SolarEdge",
            model=device.part_number or device.type,
            name=device.name or device.type,
            sw_version=device.firmware_version or device.firmware,
            serial_number=serial,
            via_device=(DOMAIN, str(coordinator.site_id)),
        )
