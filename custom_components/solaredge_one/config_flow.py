"""Config flow for SolarEdge ONE."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from aiosolaredge_one import (
    Site,
    SolarEdgeAuthError,
    SolarEdgeConnectionError,
    SolarEdgeError,
    SolarEdgeOneClient,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ACCOUNT_KEY,
    CONF_API_KEY,
    CONF_CALLS_PER_MINUTE,
    CONF_MONTHLY_BUDGET,
    CONF_PLAN_TYPE,
    CONF_SAFETY_FACTOR,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DEFAULT_CALLS_PER_MINUTE,
    DEFAULT_MONTHLY_BUDGET,
    DEFAULT_SAFETY_FACTOR,
    DOMAIN,
    PLAN_FLEET,
    PLANS,
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PLAN_TYPE, default=PLAN_FLEET): SelectSelector(
            SelectSelectorConfig(
                options=PLANS,
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="plan_type",
            )
        ),
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_ACCOUNT_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_ACCOUNT_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class SolarEdgeOneConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SolarEdge ONE."""

    VERSION = 1

    def __init__(self) -> None:
        self._auth: dict[str, Any] = {}
        self._sites: list[Site] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SolarEdgeOneOptionsFlow:
        """Return the options flow for tuning the credit budget / rate limits."""
        return SolarEdgeOneOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials, validate, then pick a site."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._auth = {
                CONF_PLAN_TYPE: user_input[CONF_PLAN_TYPE],
                CONF_API_KEY: user_input[CONF_API_KEY],
            }
            if user_input.get(CONF_ACCOUNT_KEY):
                self._auth[CONF_ACCOUNT_KEY] = user_input[CONF_ACCOUNT_KEY]
            try:
                sites = await self._fetch_sites(self._auth)
            except SolarEdgeAuthError:
                errors["base"] = "invalid_auth"
            except SolarEdgeConnectionError:
                errors["base"] = "cannot_connect"
            except SolarEdgeError:
                errors["base"] = "unknown"
            else:
                configured = self._configured_site_ids()
                self._sites = [
                    s for s in sites if s.is_active and str(s.site_id) not in configured
                ]
                if not self._sites:
                    return self.async_abort(reason="no_sites")
                if len(self._sites) == 1:
                    return await self._create_entry(self._sites[0])
                return await self.async_step_select_site()

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_select_site(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which site to add (fleet accounts with multiple sites)."""
        if user_input is not None:
            site = next(
                s for s in self._sites if str(s.site_id) == user_input[CONF_SITE_ID]
            )
            return await self._create_entry(site)

        options = {
            str(s.site_id): (s.name or f"Site {s.site_id}") for s in self._sites
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_SITE_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=value, label=label)
                            for value, label in options.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="select_site", data_schema=schema)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when credentials become invalid."""
        self._auth = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm new credentials during reauth."""
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {**self._auth, **user_input}
            try:
                await self._fetch_sites(merged)
            except SolarEdgeAuthError:
                errors["base"] = "invalid_auth"
            except SolarEdgeConnectionError:
                errors["base"] = "cannot_connect"
            except SolarEdgeError:
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates=user_input
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors
        )

    async def _create_entry(self, site: Site) -> ConfigFlowResult:
        await self.async_set_unique_id(str(site.site_id))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=site.name or f"Site {site.site_id}",
            data={
                **self._auth,
                CONF_SITE_ID: site.site_id,
                CONF_SITE_NAME: site.name,
            },
        )

    async def _fetch_sites(self, auth: Mapping[str, Any]) -> list[Site]:
        session = async_get_clientsession(self.hass)
        client = SolarEdgeOneClient(
            session,
            api_key=auth[CONF_API_KEY],
            account_key=auth.get(CONF_ACCOUNT_KEY),
        )
        return await client.get_sites()

    def _configured_site_ids(self) -> set[str | None]:
        return {entry.unique_id for entry in self._async_current_entries()}


class SolarEdgeOneOptionsFlow(OptionsFlow):
    """Tune the adaptive scheduler: monthly budget, call rate, safety factor."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and persist the rate-limiting options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        entry = self.config_entry

        def _cur(key: str, default: Any) -> Any:
            return entry.options.get(key, entry.data.get(key, default))

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MONTHLY_BUDGET,
                    default=_cur(CONF_MONTHLY_BUDGET, DEFAULT_MONTHLY_BUDGET),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=1_000_000, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_CALLS_PER_MINUTE,
                    default=_cur(CONF_CALLS_PER_MINUTE, DEFAULT_CALLS_PER_MINUTE),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=1_000, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_SAFETY_FACTOR,
                    default=_cur(CONF_SAFETY_FACTOR, DEFAULT_SAFETY_FACTOR),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.1, max=1.0, step=0.05, mode=NumberSelectorMode.SLIDER
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
