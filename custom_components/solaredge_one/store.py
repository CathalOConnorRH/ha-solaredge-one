"""Persistence for the per-entry credit ledger.

The monthly credit quota is not exposed by the API, so usage is tracked locally
and must survive restarts (otherwise every reboot would reset the budget and let
polling overspend). We persist the ledger with HA's ``Store`` keyed per entry.
"""

from __future__ import annotations

from aiosolaredge_one import CreditLedger
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, LEDGER_STORAGE_KEY, STORAGE_VERSION


def ledger_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, object]]:
    """Return the Store for a config entry's credit ledger."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.{LEDGER_STORAGE_KEY}")


async def load_ledger(
    store: Store[dict[str, object]], monthly_budget: int
) -> CreditLedger:
    """Restore the ledger from storage, falling back to a fresh one.

    ``monthly_budget`` always comes from the (possibly just-changed) config so
    that editing the budget in options takes effect immediately; only the
    rolling ``used``/``month`` counters are restored from disk.
    """
    ledger = CreditLedger(monthly_budget=monthly_budget)
    stored = await store.async_load()
    if stored:
        used = stored.get("used", 0)
        ledger.used = int(used) if isinstance(used, (int, float, str)) else 0
        month = stored.get("month")
        ledger.month = month if isinstance(month, str) else None
    return ledger


async def save_ledger(store: Store[dict[str, object]], ledger: CreditLedger) -> None:
    """Persist the ledger's rolling counters."""
    await store.async_save(ledger.to_dict())
