"""Config flow for SolarEdge ONE."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from aiohttp import ClientError
from aiosolaredge_one import (
    Site,
    SolarEdgeAuthError,
    SolarEdgeConnectionError,
    SolarEdgeError,
    SolarEdgeNotFoundError,
    SolarEdgeOneClient,
)
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler
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
    AUTH_API_KEY,
    AUTH_OAUTH,
    CONF_ACCOUNT_KEY,
    CONF_API_KEY,
    CONF_AUTH_TYPE,
    CONF_CALLS_PER_MINUTE,
    CONF_MONTHLY_BUDGET,
    CONF_PLAN_TYPE,
    CONF_REDIRECT_URL,
    CONF_SAFETY_FACTOR,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DEFAULT_CALLS_PER_MINUTE,
    DEFAULT_MONTHLY_BUDGET,
    DEFAULT_SAFETY_FACTOR,
    DOMAIN,
    LOGGER,
    PLAN_FLEET,
    PLAN_SITE_OWNER,
    PLANS,
)
from .oauth import async_forget_flow, callback_url, parse_redirect_url

# SolarEdge authorization codes are short-lived and single-use.
OAUTH_TOKEN_TIMEOUT = 30

API_KEY_SCHEMA = vol.Schema(
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

SITE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SITE_ID): NumberSelector(
            NumberSelectorConfig(min=1, step=1, mode=NumberSelectorMode.BOX)
        )
    }
)

PASTE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_REDIRECT_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        )
    }
)


class SolarEdgeOneConfigFlow(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle a config flow for SolarEdge ONE.

    Two auth paths: OAuth 2.0 Site Access (homeowners; Client ID/Secret held in
    HA's application credentials) and static API keys (Fleet Access).
    """

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._auth: dict[str, Any] = {}
        self._sites: list[Site] = []
        self._oauth_data: dict[str, Any] = {}
        self._authorize_url: str | None = None
        self._oauth_error: str | None = None

    @property
    def logger(self) -> logging.Logger:
        """Logger used by the OAuth2 flow helper."""
        return LOGGER

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
        """Choose how to authenticate."""
        return self.async_show_menu(step_id="user", menu_options=["oauth", "api_key"])

    async def async_step_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect API-key credentials, validate, then pick a site."""
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
            step_id="api_key", data_schema=API_KEY_SCHEMA, errors=errors
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

    # -- OAuth 2.0 Site Access ----------------------------------------------
    async def async_step_oauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start Site Access: pick the application credential to sign in with."""
        return await self.async_step_pick_implementation()

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace the helper's generic external step (SolarEdge drops ``state``)."""
        return await self.async_step_sign_in()

    async def async_step_sign_in(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the redirect URI, then sign in automatically or by pasting."""
        return self.async_show_menu(
            step_id="sign_in",
            menu_options=["authorize", "paste"],
            description_placeholders={"callback_url": callback_url(self.hass)},
        )

    async def async_step_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open SolarEdge's consent page; our callback view resumes this step."""
        if user_input is None:
            return self.async_external_step(
                step_id="authorize", url=await self._async_authorize_url()
            )
        self.external_data = user_input
        next_step = "authorize_rejected" if "error" in user_input else "creation"
        return self.async_external_step_done(next_step_id=next_step)

    async def async_step_paste(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual sign-in: paste the address SolarEdge redirected the tab to.

        For when the redirect can't reach Home Assistant (e.g. the registered
        redirect URI differs), or after a refused code.
        """
        errors: dict[str, str] = {}
        if self._oauth_error:
            errors["base"] = self._oauth_error
            self._oauth_error = None
        if user_input is not None:
            parsed = parse_redirect_url(user_input[CONF_REDIRECT_URL])
            if "code" in parsed:
                self.external_data = parsed
                return await self.async_step_creation()
            errors["base"] = "invalid_redirect_url"
        return self.async_show_form(
            step_id="paste",
            data_schema=PASTE_SCHEMA,
            errors=errors,
            description_placeholders={
                "authorize_url": await self._async_authorize_url(),
            },
        )

    async def async_step_creation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Exchange the code; on failure fall back to the paste step with an error."""
        try:
            async with asyncio.timeout(OAUTH_TOKEN_TIMEOUT):
                token = await self.flow_impl.async_resolve_external_data(
                    self.external_data
                )
            token["expires_in"] = int(token["expires_in"])
        except (ClientError, TimeoutError, KeyError, ValueError) as err:
            LOGGER.warning("SolarEdge token exchange failed: %s", err)
            self._oauth_error = "oauth_failed"
            return await self.async_step_paste()
        token["expires_at"] = time.time() + token["expires_in"]
        return await self.async_oauth_create_entry(
            {"auth_implementation": self.flow_impl.domain, "token": token}
        )

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Finish reauth here; a new setup continues to the site step."""
        site_id = self.external_data.get(CONF_SITE_ID)
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if site_id and int(site_id) != int(entry.data[CONF_SITE_ID]):
                return self.async_abort(reason="wrong_site")
            return self.async_update_reload_and_abort(entry, data_updates=data)
        self._oauth_data = data
        if site_id:
            return await self.async_step_site({CONF_SITE_ID: site_id})
        return await self.async_step_site()

    async def async_step_site(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the authorized site (taken from the redirect when present)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            site_id = int(user_input[CONF_SITE_ID])
            try:
                site = await self._oauth_client().get_site_details(site_id)
            except SolarEdgeNotFoundError:
                errors["base"] = "site_not_found"
            except SolarEdgeAuthError:
                errors["base"] = "site_not_authorized"
            except SolarEdgeConnectionError:
                errors["base"] = "cannot_connect"
            except SolarEdgeError:
                errors["base"] = "unknown"
            else:
                data = {
                    **self._oauth_data,
                    CONF_AUTH_TYPE: AUTH_OAUTH,
                    CONF_PLAN_TYPE: PLAN_SITE_OWNER,
                    CONF_SITE_ID: site_id,
                    CONF_SITE_NAME: site.name,
                }
                existing = await self.async_set_unique_id(str(site_id))
                if (
                    existing is not None
                    and existing.data.get(CONF_AUTH_TYPE, AUTH_API_KEY) != AUTH_OAUTH
                ):
                    # Same site already set up with an API key (e.g. a retired
                    # key): switch it to Site Access in place, keeping its
                    # entities and history, and drop the old key.
                    return self.async_update_reload_and_abort(
                        existing, data=data, reason="migrated_to_oauth"
                    )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=site.name or f"Site {site_id}", data=data
                )
        return self.async_show_form(
            step_id="site",
            data_schema=self.add_suggested_values_to_schema(SITE_SCHEMA, user_input),
            errors=errors,
        )

    async def _async_authorize_url(self) -> str:
        # Generated once per flow: each call registers a new external_id.
        if self._authorize_url is None:
            self._authorize_url = await self.async_generate_authorize_url()
        return self._authorize_url

    def _oauth_client(self) -> SolarEdgeOneClient:
        access_token = str(self._oauth_data["token"]["access_token"])

        async def _token() -> str:
            return access_token

        return SolarEdgeOneClient(
            async_get_clientsession(self.hass), token_provider=_token
        )

    @callback
    def async_remove(self) -> None:
        """Forget this flow's pending sign-in when the flow goes away."""
        async_forget_flow(self.hass, self.flow_id)

    # -- reauth ----------------------------------------------------------------
    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when credentials become invalid."""
        self._auth = dict(entry_data)
        if entry_data.get(CONF_AUTH_TYPE) == AUTH_OAUTH:
            return await self.async_step_reauth_oauth()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_oauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the site owner to approve access again (grant expired/revoked)."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_oauth")
        return await self.async_step_pick_implementation()

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
