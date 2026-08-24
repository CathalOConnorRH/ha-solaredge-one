"""Constants for the SolarEdge ONE integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "solaredge_one"
LOGGER = logging.getLogger(__package__)

# Config entry / flow keys
CONF_PLAN_TYPE = "plan_type"
CONF_API_KEY = "api_key"
CONF_ACCOUNT_KEY = "account_key"
CONF_SITE_ID = "site_id"
CONF_SITE_NAME = "site_name"
CONF_MONTHLY_BUDGET = "monthly_credit_budget"
CONF_CALLS_PER_MINUTE = "calls_per_minute"
CONF_SAFETY_FACTOR = "budget_safety_factor"

# Plan types (see prd/00-overview.md)
PLAN_FLEET = "fleet"
PLAN_SITE_OWNER = "site_owner"
PLANS = [PLAN_FLEET, PLAN_SITE_OWNER]

# Defaults from the reference My Fleet token; user-editable via the options flow.
DEFAULT_MONTHLY_BUDGET = 2000
DEFAULT_CALLS_PER_MINUTE = 10
DEFAULT_SAFETY_FACTOR = 0.7

# Adaptive scheduler bounds (see prd/02-api-and-rate-limiting.md).
# One poll cycle issues 3 calls: overview + power + alerts. The device inventory
# is fetched once at setup (rarely changes), not per cycle.
CREDITS_PER_CYCLE = 3
MIN_INTERVAL = timedelta(minutes=1)
MAX_INTERVAL = timedelta(hours=6)
NIGHT_FACTOR = 3.0

# Lifetime / this-year / this-month energy totals come from /energy, which is
# separate from the per-cycle overview call. These totals change slowly, so we
# refresh them at most this often (and skip at night, when nothing is produced),
# caching the last values between polls. Two extra calls per refresh:
# one YEAR-resolution call (lifetime + year-to-date) + one MONTH call.
ENERGY_REFRESH_INTERVAL = timedelta(minutes=60)

# 429 backoff: exponential from 1 min, capped at 1 hour.
BACKOFF_BASE = timedelta(minutes=1)
BACKOFF_CAP = timedelta(hours=1)

# Persisted credit ledger (helpers.storage).
STORAGE_VERSION = 1
LEDGER_STORAGE_KEY = "ledger"

# Repair issue raised when projected month-end spend exceeds the budget.
ISSUE_OVER_BUDGET = "over_budget"

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]
