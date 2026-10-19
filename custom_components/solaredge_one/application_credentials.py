"""Application credentials for SolarEdge ONE Site Access (OAuth 2.0)."""

from __future__ import annotations

import secrets
from ipaddress import ip_address
from typing import Any, cast

from aiosolaredge_one import (
    OAUTH2_AUTHORIZE_URL,
    OAUTH2_MAX_ACCESS_DURATION,
    OAUTH2_SCOPE_DEVICE_DATA,
    OAUTH2_SCOPE_SITE_DATA,
    OAUTH2_TOKEN_URL,
)
from homeassistant.components.application_credentials import (
    AuthImplementation,
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

from .const import DEVELOPER_PORTAL_URL, LOGGER, OAUTH_CALLBACK_PATH
from .oauth import async_register_view, callback_url, pending_flows

AUTHORIZATION_SERVER = AuthorizationServer(
    authorize_url=OAUTH2_AUTHORIZE_URL, token_url=OAUTH2_TOKEN_URL
)
SCOPES = f"{OAUTH2_SCOPE_SITE_DATA} {OAUTH2_SCOPE_DEVICE_DATA}"


class SolarEdgeOneOAuth2Implementation(AuthImplementation):
    """SolarEdge Connect differs from a stock OAuth 2.0 provider in three ways.

    - The redirect is correlated through ``external_id`` (``state`` is not
      returned), so the authorize URL carries one and points at our own view.
    - The token endpoint takes a JSON body rather than a form.
    - The token request takes no ``redirect_uri`` (the registered one is used).
    """

    @property
    def redirect_uri(self) -> str:
        """Our own callback view, not HA's generic one."""
        return callback_url(self.hass)

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        """Build the consent URL and remember which flow it belongs to."""
        async_register_view(self.hass)
        external_id = secrets.token_urlsafe(16)
        pending_flows(self.hass)[external_id] = flow_id
        return str(
            URL(self.authorize_url).with_query(
                {
                    "client_id": self.client_id,
                    "scope": SCOPES,
                    # Ask for the longest grant the site owner may approve.
                    "access_duration": str(OAUTH2_MAX_ACCESS_DURATION),
                    "external_id": external_id,
                    "redirect_uri": self.redirect_uri,
                }
            )
        )

    async def async_resolve_external_data(self, external_data: Any) -> dict[str, Any]:
        """Exchange the (single-use) authorization code for tokens."""
        return await self._token_request(
            {"grant_type": "authorization_code", "code": external_data["code"]}
        )

    async def _token_request(self, data: dict[str, Any]) -> dict[str, Any]:
        """POST the token request as JSON; raise ``ClientResponseError`` on failure."""
        data["client_id"] = self.client_id
        data["client_secret"] = self.client_secret
        session = async_get_clientsession(self.hass)
        async with session.post(self.token_url, json=data) as resp:
            if resp.status >= 400:
                LOGGER.warning(
                    "SolarEdge token request (%s) failed with HTTP %s: %s",
                    data.get("grant_type"),
                    resp.status,
                    (await resp.text())[:200],
                )
            resp.raise_for_status()
            return cast(dict[str, Any], await resp.json(content_type=None))


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return the SolarEdge Connect authorization server."""
    return AUTHORIZATION_SERVER


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> SolarEdgeOneOAuth2Implementation:
    """Return the SolarEdge-specific OAuth implementation for a credential."""
    return SolarEdgeOneOAuth2Implementation(
        hass, auth_domain, credential, AUTHORIZATION_SERVER
    )


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Placeholders for the 'Add application credentials' dialog.

    This dialog is rendered over the websocket, so the browser's address
    (``HA-Frontend-Base``) isn't available and ``callback_url`` falls back to
    HA's configured URLs. If those only give a plain-HTTP or IP address (which
    SolarEdge rejects), show a template instead of a misleading URL; the
    sign-in step, which does see the browser's address, shows the exact value.
    """
    redirect_url = callback_url(hass)
    if not _is_https_domain(redirect_url):
        redirect_url = f"https://<your-home-assistant-domain>{OAUTH_CALLBACK_PATH}"
    return {
        "developer_portal_url": DEVELOPER_PORTAL_URL,
        "redirect_url": redirect_url,
    }


def _is_https_domain(url: str) -> bool:
    """True for an https:// URL whose host is a name, not an IP address."""
    parsed = URL(url)
    if parsed.scheme != "https" or not parsed.host:
        return False
    try:
        ip_address(parsed.host)
    except ValueError:
        return True
    return False
