"""The SolarEdge ONE integration."""

from __future__ import annotations

from dataclasses import dataclass, field

from aiosolaredge_one import (
    CreditLedger,
    Device,
    Site,
    SolarEdgeError,
    SolarEdgeOneClient,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ACCOUNT_KEY,
    CONF_API_KEY,
    CONF_MONTHLY_BUDGET,
    CONF_SITE_ID,
    DEFAULT_MONTHLY_BUDGET,
    DOMAIN,
    LOGGER,
    PLATFORMS,
)
from .coordinator import SolarEdgeOneCoordinator
from .store import ledger_store, load_ledger


@dataclass(slots=True)
class SolarEdgeOneRuntimeData:
    """Objects stored on the config entry at runtime."""

    client: SolarEdgeOneClient
    coordinator: SolarEdgeOneCoordinator
    ledger: CreditLedger
    devices: list[Device] = field(default_factory=list)
    site: Site | None = None


type SolarEdgeOneConfigEntry = ConfigEntry[SolarEdgeOneRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarEdgeOneConfigEntry
) -> bool:
    """Set up SolarEdge ONE from a config entry."""
    session = async_get_clientsession(hass)

    # Budget can be edited in options; options win over the original config data.
    monthly_budget = int(
        entry.options.get(
            CONF_MONTHLY_BUDGET,
            entry.data.get(CONF_MONTHLY_BUDGET, DEFAULT_MONTHLY_BUDGET),
        )
    )
    store = ledger_store(hass, entry.entry_id)
    ledger = await load_ledger(store, monthly_budget)

    client = SolarEdgeOneClient(
        session,
        api_key=entry.data[CONF_API_KEY],
        account_key=entry.data.get(CONF_ACCOUNT_KEY),
        ledger=ledger,
    )
    site_id = int(entry.data[CONF_SITE_ID])

    # One-off metadata fetched before the coordinator so it can seed the install
    # date (for the lifetime-energy window) and know whether a battery exists
    # (to gate storage telemetry). Failures here must not block setup — the
    # site-level sensors still work without them.
    site: Site | None = None
    try:
        site = await client.get_site_details(site_id)
    except SolarEdgeError as err:
        LOGGER.debug("Could not fetch site details for site %s: %s", site_id, err)

    devices: list[Device] = []
    try:
        devices = await client.get_devices(site_id)
    except SolarEdgeError as err:
        LOGGER.debug("Could not fetch device inventory for site %s: %s", site_id, err)

    coordinator = SolarEdgeOneCoordinator(
        hass,
        entry,
        client,
        site_id,
        ledger,
        store,
        install_date=site.installation_date if site else None,
        has_battery=_has_battery(devices),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = SolarEdgeOneRuntimeData(
        client=client,
        coordinator=coordinator,
        ledger=ledger,
        devices=devices,
        site=site,
    )
    _register_devices(hass, entry, site_id, entry.title, site, devices)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SolarEdgeOneConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: SolarEdgeOneConfigEntry
) -> None:
    """Reload the entry when its options (budget/limits) change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _has_battery(devices: list[Device]) -> bool:
    """True when the inventory contains a battery/storage device."""
    return any(
        (device.type or "").upper() in ("BATTERY", "STORAGE")
        or "BATTER" in (device.type or "").upper()
        for device in devices
    )


def _register_devices(
    hass: HomeAssistant,
    entry: SolarEdgeOneConfigEntry,
    site_id: int,
    site_name: str,
    site: Site | None,
    devices: list[Device],
) -> None:
    """Create the Site device and its child devices (inverters, meters...).

    Child devices are registered eagerly so the device tree is correct even
    before per-device entities exist (those arrive in a later phase).
    """
    registry = dr.async_get(hass)
    # Peak (installed) DC capacity, if the site details reported it, shown on the
    # site device as its hardware version (kWp).
    hw_version = (
        f"{site.peak_power:g} kWp" if site and site.peak_power is not None else None
    )
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, str(site_id))},
        manufacturer="SolarEdge",
        model="SolarEdge ONE Site",
        name=site_name,
        hw_version=hw_version,
        configuration_url="https://monitoring.solaredge.com",
    )
    seen: set[str] = set()
    for device in devices:
        serial = device.serial_number
        if not serial or serial in seen:
            continue
        seen.add(serial)
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, serial)},
            manufacturer=device.manufacturer or "SolarEdge",
            model=device.part_number or device.type,
            name=device.name or device.type,
            sw_version=device.firmware_version or device.firmware,
            serial_number=serial,
            via_device=(DOMAIN, str(site_id)),
        )
