"""OAuth 2.0 Site Access plumbing for SolarEdge ONE.

SolarEdge Connect redirects the browser to the app's registered redirect URI
with ``code``, ``site_id`` and the ``external_id`` we sent, but never returns
``state``. HA's generic OAuth callback (and my.home-assistant.io) need
``state`` to find the config flow, so — like the core Plex integration — we
serve our own callback and map ``external_id`` back to the flow.
"""

from __future__ import annotations

from html import escape
from typing import Any

from aiohttp import ClientError, ClientResponseError, web
from aiosolaredge_one import (
    SolarEdgeAuthError,
    SolarEdgeConnectionError,
    TokenProvider,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers import http
from homeassistant.helpers.config_entry_oauth2_flow import (
    HEADER_FRONTEND_BASE,
    OAuth2Session,
)
from homeassistant.helpers.http import KEY_HASS, HomeAssistantView
from homeassistant.helpers.network import NoURLAvailableError, get_url
from yarl import URL

from .const import CONF_SITE_ID, DOMAIN, LOGGER, OAUTH_CALLBACK_PATH

DATA_PENDING = f"{DOMAIN}_oauth_pending"
DATA_VIEW = f"{DOMAIN}_oauth_view"


def callback_url(hass: HomeAssistant) -> str:
    """Return the redirect URI as the user's browser reaches Home Assistant.

    Prefers the frontend's own base URL (what the user is actually browsing),
    then HA's configured URL. This exact value must be registered as the
    redirect URI of the SolarEdge app.

    SolarEdge only accepts HTTPS redirect URIs on a registered domain (or
    ``http://localhost``), so when the user is browsing over plain HTTP we
    prefer a configured HTTPS, non-IP URL if HA has one. SolarEdge always
    redirects to the URI registered on the app, so this value is guidance for
    the user; any address that reaches HA serves the callback.
    """
    request = http.current_request.get()
    base = request.headers.get(HEADER_FRONTEND_BASE) if request is not None else None
    if base and base.startswith("https://"):
        return f"{base.rstrip('/')}{OAUTH_CALLBACK_PATH}"
    try:
        return f"{get_url(hass, require_ssl=True, allow_ip=False)}{OAUTH_CALLBACK_PATH}"
    except NoURLAvailableError:
        pass
    if base:
        return f"{base.rstrip('/')}{OAUTH_CALLBACK_PATH}"
    try:
        return f"{get_url(hass)}{OAUTH_CALLBACK_PATH}"
    except NoURLAvailableError:
        return f"http://homeassistant.local:8123{OAUTH_CALLBACK_PATH}"


def pending_flows(hass: HomeAssistant) -> dict[str, str]:
    """Map of ``external_id`` → config flow id for sign-ins in progress."""
    pending: dict[str, str] = hass.data.setdefault(DATA_PENDING, {})
    return pending


@callback
def async_forget_flow(hass: HomeAssistant, flow_id: str) -> None:
    """Drop any pending sign-in that belongs to ``flow_id``."""
    pending = pending_flows(hass)
    for key in [k for k, v in pending.items() if v == flow_id]:
        pending.pop(key)


@callback
def async_register_view(hass: HomeAssistant) -> None:
    """Register the callback view (once per HA run)."""
    if not hass.data.get(DATA_VIEW):
        hass.http.register_view(SolarEdgeOneOAuthCallbackView)
        hass.data[DATA_VIEW] = True


def parse_redirect(query: Any) -> dict[str, str]:
    """Extract ``code``/``error`` and ``site_id`` from a redirect query string."""
    result: dict[str, str] = {}
    if code := query.get("code"):
        result["code"] = code
    elif error := query.get("error"):
        result["error"] = error
    if (site_id := query.get(CONF_SITE_ID)) and str(site_id).isdigit():
        result[CONF_SITE_ID] = str(site_id)
    return result


def parse_redirect_url(url: str) -> dict[str, str]:
    """Parse a redirect address pasted by the user (manual sign-in)."""
    try:
        return parse_redirect(URL(url.strip()).query)
    except ValueError:
        return {}


def _page(message: str, status: int = 200, *, close: bool = False) -> web.Response:
    script = "<script>window.close()</script>" if close else ""
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>SolarEdge ONE</title></head>"
        "<body style='font-family:system-ui,sans-serif;max-width:32rem;"
        f"margin:3rem auto;padding:0 1rem'>{script}<h2>SolarEdge ONE</h2>"
        f"<p>{escape(message)}</p></body></html>"
    )
    return web.Response(status=status, content_type="text/html", text=body)


class SolarEdgeOneOAuthCallbackView(HomeAssistantView):
    """Receive SolarEdge's redirect and resume the matching config flow."""

    url = OAUTH_CALLBACK_PATH
    name = f"auth:{DOMAIN}:callback"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Hand the authorization code (or error) to the waiting config flow."""
        hass = request.app[KEY_HASS]
        pending = pending_flows(hass)
        if external_id := request.query.get("external_id"):
            flow_id = pending.get(external_id)
        else:
            # Without external_id, only accept when exactly one sign-in is
            # pending, so a stray code can't land in the wrong flow.
            flow_id = next(iter(pending.values())) if len(pending) == 1 else None
        if flow_id is None:
            return _page(
                "This sign-in doesn't match a setup in progress (it may have "
                "expired). Start the SolarEdge ONE setup again in Home Assistant.",
                400,
            )

        user_input = parse_redirect(request.query)
        if "code" not in user_input and "error" not in user_input:
            return _page("SolarEdge did not send an authorization code.", 400)

        async_forget_flow(hass, flow_id)
        try:
            await hass.config_entries.flow.async_configure(
                flow_id=flow_id, user_input=user_input
            )
        except UnknownFlow:
            return _page(
                "The setup dialog was closed. Start the SolarEdge ONE setup again.",
                400,
            )
        LOGGER.debug("Resumed SolarEdge ONE OAuth flow %s", flow_id)
        if "error" in user_input:
            return _page("Access was declined. You can close this tab.", close=True)
        return _page("SolarEdge connected. You can close this tab.", close=True)


def token_provider(session: OAuth2Session) -> TokenProvider:
    """Adapt an HA ``OAuth2Session`` into the client's async token provider.

    Refresh failures are mapped onto the library's exceptions so the
    coordinator's existing handling applies: a rejected refresh token (4xx)
    becomes ``SolarEdgeAuthError`` → reauth; anything else is transient.
    """

    async def _get_token() -> str:
        try:
            await session.async_ensure_token_valid()
        except ClientResponseError as err:
            if 400 <= err.status < 500 and err.status != 429:
                raise SolarEdgeAuthError(
                    "SolarEdge rejected the refresh token — re-authorize access",
                    status=err.status,
                ) from err
            raise SolarEdgeConnectionError(f"Token refresh failed: {err}") from err
        except ClientError as err:
            raise SolarEdgeConnectionError(f"Token refresh failed: {err}") from err
        return str(session.token["access_token"])

    return _get_token
