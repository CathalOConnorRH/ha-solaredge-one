"""OAuth 2.0 Site Access tests for SolarEdge ONE."""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiosolaredge_one import (
    OAUTH2_TOKEN_URL,
    Site,
    SolarEdgeAuthError,
    SolarEdgeConnectionError,
    SolarEdgeNotFoundError,
)
from homeassistant import config_entries
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from yarl import URL

from custom_components.solaredge_one.application_credentials import (
    async_get_description_placeholders,
)
from custom_components.solaredge_one.const import (
    AUTH_OAUTH,
    CONF_AUTH_TYPE,
    CONF_PLAN_TYPE,
    CONF_REDIRECT_URL,
    CONF_SITE_ID,
    DOMAIN,
    OAUTH_CALLBACK_PATH,
    PLAN_SITE_OWNER,
)
from custom_components.solaredge_one.oauth import callback_url, token_provider

CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"
SITE_ID = 3066774
CALLBACK = f"https://example.com{OAUTH_CALLBACK_PATH}"
TOKEN = {
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    # SolarEdge returns expires_in as a string; the flow must coerce it.
    "expires_in": "3600",
    "token_type": "Bearer",
}


@pytest.fixture(autouse=True)
async def setup_credentials(hass: HomeAssistant) -> None:
    """Register a Site Access application credential."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential(CLIENT_ID, CLIENT_SECRET), "cred"
    )


@pytest.fixture
def mock_site_client():
    """Patch the flow's client so get_site_details returns a site."""
    with patch(
        "custom_components.solaredge_one.config_flow.SolarEdgeOneClient"
    ) as mock_cls:
        client = mock_cls.return_value
        client.get_site_details = AsyncMock(
            return_value=Site(site_id=SITE_ID, name="My Home")
        )
        yield mock_cls


async def _start_sign_in(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "oauth"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "sign_in"
    assert result["description_placeholders"]["callback_url"] == CALLBACK
    return result


@pytest.mark.usefixtures("current_request_with_host")
async def test_oauth_automatic_sign_in(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    mock_site_client,
) -> None:
    """Consent → our callback → token exchange → entry, site taken from redirect."""
    result = await _start_sign_in(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "authorize"}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    url = URL(result["url"])
    assert url.query["client_id"] == CLIENT_ID
    assert url.query["scope"] == "SITE_DATA DEVICE_DATA"
    assert url.query["access_duration"] == "24"
    assert url.query["redirect_uri"] == CALLBACK
    assert "state" not in url.query
    external_id = url.query["external_id"]

    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN)
    client = await hass_client_no_auth()
    resp = await client.get(
        OAUTH_CALLBACK_PATH,
        params={"code": "abc", "site_id": str(SITE_ID), "external_id": external_id},
    )
    assert resp.status == HTTPStatus.OK

    with patch("custom_components.solaredge_one.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Home"
    data = result["data"]
    assert data[CONF_AUTH_TYPE] == AUTH_OAUTH
    assert data[CONF_PLAN_TYPE] == PLAN_SITE_OWNER
    assert data[CONF_SITE_ID] == SITE_ID
    assert data["auth_implementation"] == "cred"
    assert data["token"]["access_token"] == "access-1"
    assert data["token"]["expires_in"] == 3600
    assert result["result"].unique_id == str(SITE_ID)

    # Token request is a JSON body with the client credentials, no redirect_uri.
    _, _, body, _ = aioclient_mock.mock_calls[0]
    assert body == {
        "grant_type": "authorization_code",
        "code": "abc",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    # The site was validated with the freshly issued bearer token.
    assert "token_provider" in mock_site_client.call_args.kwargs


@pytest.mark.usefixtures("current_request_with_host")
async def test_oauth_paste_then_site_step(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_site_client
) -> None:
    """Pasted redirect without site_id → ask for the site ID."""
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN)
    result = await _start_sign_in(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "paste"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "paste"
    assert "external_id=" in result["description_placeholders"]["authorize_url"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: "https://example.com/nope"}
    )
    assert result["errors"] == {"base": "invalid_redirect_url"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: f"{CALLBACK}?code=abc"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "site"

    with patch("custom_components.solaredge_one.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SITE_ID: SITE_ID}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SITE_ID] == SITE_ID


@pytest.mark.usefixtures("current_request_with_host")
async def test_oauth_migrates_existing_api_key_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_site_client
) -> None:
    """Site Access on a site set up with an API key converts that entry in place."""
    legacy = MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(SITE_ID),
        title="Gorsehill",
        data={
            CONF_PLAN_TYPE: PLAN_SITE_OWNER,
            "api_key": "retired-key",
            CONF_SITE_ID: SITE_ID,
        },
    )
    legacy.add_to_hass(hass)
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN)

    result = await _start_sign_in(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "paste"}
    )
    with patch("custom_components.solaredge_one.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_REDIRECT_URL: f"{CALLBACK}?code=abc&site_id={SITE_ID}"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "migrated_to_oauth"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert legacy.title == "Gorsehill"
    assert legacy.data[CONF_AUTH_TYPE] == AUTH_OAUTH
    assert legacy.data["token"]["access_token"] == "access-1"
    assert "api_key" not in legacy.data


@pytest.mark.usefixtures("current_request_with_host")
async def test_oauth_duplicate_oauth_site_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_site_client
) -> None:
    """A site already set up with Site Access aborts as already_configured."""
    _oauth_entry().add_to_hass(hass)
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN)
    result = await _start_sign_in(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "paste"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_REDIRECT_URL: f"{CALLBACK}?code=abc&site_id={SITE_ID}"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("current_request_with_host")
@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (SolarEdgeNotFoundError("nope"), "site_not_found"),
        (SolarEdgeAuthError("denied"), "site_not_authorized"),
        (SolarEdgeConnectionError("down"), "cannot_connect"),
    ],
)
async def test_oauth_site_step_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_site_client,
    side_effect: Exception,
    error: str,
) -> None:
    """Site validation failures re-show the site form with an error."""
    mock_site_client.return_value.get_site_details.side_effect = side_effect
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN)
    result = await _start_sign_in(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "paste"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_REDIRECT_URL: f"{CALLBACK}?code=abc&site_id={SITE_ID}"},
    )
    assert result["step_id"] == "site"
    assert result["errors"] == {"base": error}


@pytest.mark.usefixtures("current_request_with_host")
async def test_oauth_token_exchange_failure_falls_back_to_paste(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refused code shows the paste form with an error instead of aborting."""
    aioclient_mock.post(OAUTH2_TOKEN_URL, status=HTTPStatus.BAD_REQUEST)
    result = await _start_sign_in(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "paste"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_REDIRECT_URL: f"{CALLBACK}?code=used"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "paste"
    assert result["errors"] == {"base": "oauth_failed"}


@pytest.mark.usefixtures("current_request_with_host")
async def test_oauth_access_declined(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """The site owner declining consent aborts the flow."""
    result = await _start_sign_in(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "authorize"}
    )
    external_id = URL(result["url"]).query["external_id"]
    client = await hass_client_no_auth()
    resp = await client.get(
        OAUTH_CALLBACK_PATH,
        params={"error": "access_denied", "external_id": external_id},
    )
    assert resp.status == HTTPStatus.OK

    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "user_rejected_authorize"


@pytest.mark.usefixtures("current_request_with_host")
async def test_callback_rejects_unknown_external_id(
    hass: HomeAssistant, hass_client_no_auth
) -> None:
    """A redirect that matches no pending sign-in is refused."""
    result = await _start_sign_in(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "authorize"}
    )
    client = await hass_client_no_auth()
    resp = await client.get(
        OAUTH_CALLBACK_PATH, params={"code": "abc", "external_id": "forged"}
    )
    assert resp.status == HTTPStatus.BAD_REQUEST


async def test_oauth_without_credentials_aborts(hass: HomeAssistant) -> None:
    """No application credential configured → helpful abort."""
    for entry in hass.config_entries.async_entries("application_credentials"):
        await hass.config_entries.async_remove(entry.entry_id)
    with patch(
        "homeassistant.helpers.config_entry_oauth2_flow.async_get_implementations",
        return_value={},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "oauth"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] in ("missing_credentials", "missing_configuration")


def _oauth_entry(token: dict[str, Any] | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(SITE_ID),
        title="My Home",
        data={
            CONF_AUTH_TYPE: AUTH_OAUTH,
            CONF_PLAN_TYPE: PLAN_SITE_OWNER,
            CONF_SITE_ID: SITE_ID,
            "auth_implementation": "cred",
            "token": token
            or {
                "access_token": "old",
                "refresh_token": "old-refresh",
                "expires_in": 3600,
                "expires_at": time.time() + 3600,
            },
        },
    )


@pytest.mark.usefixtures("current_request_with_host")
@pytest.mark.parametrize(
    ("redirect_site", "reason"),
    [(SITE_ID, "reauth_successful"), (999, "wrong_site")],
)
async def test_oauth_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    redirect_site: int,
    reason: str,
) -> None:
    """OAuth reauth re-runs consent and only accepts the same site."""
    entry = _oauth_entry()
    entry.add_to_hass(hass)
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_oauth"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "sign_in"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "paste"}
    )
    with patch("custom_components.solaredge_one.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_REDIRECT_URL: f"{CALLBACK}?code=abc&site_id={redirect_site}"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    expected = "access-1" if reason == "reauth_successful" else "old"
    assert entry.data["token"]["access_token"] == expected


async def _session(hass: HomeAssistant, entry: MockConfigEntry):
    impl = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    return config_entry_oauth2_flow.OAuth2Session(hass, entry, impl)


async def test_token_provider_returns_valid_token(hass: HomeAssistant) -> None:
    """A still-valid token is used as-is (no refresh request)."""
    entry = _oauth_entry()
    entry.add_to_hass(hass)
    provider = token_provider(await _session(hass, entry))
    assert await provider() == "old"


async def test_token_provider_refreshes_expired_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An expired token is refreshed via the JSON token endpoint and persisted."""
    entry = _oauth_entry(
        {"access_token": "old", "refresh_token": "r", "expires_at": time.time() - 10}
    )
    entry.add_to_hass(hass)
    aioclient_mock.post(OAUTH2_TOKEN_URL, json=TOKEN)

    provider = token_provider(await _session(hass, entry))
    assert await provider() == "access-1"
    assert entry.data["token"]["access_token"] == "access-1"
    _, _, body, _ = aioclient_mock.mock_calls[0]
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "r"


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (HTTPStatus.BAD_REQUEST, SolarEdgeAuthError),
        (HTTPStatus.UNAUTHORIZED, SolarEdgeAuthError),
        (HTTPStatus.TOO_MANY_REQUESTS, SolarEdgeConnectionError),
        (HTTPStatus.INTERNAL_SERVER_ERROR, SolarEdgeConnectionError),
    ],
)
async def test_token_provider_maps_refresh_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: HTTPStatus,
    exc: type[Exception],
) -> None:
    """Rejected refresh → auth error (reauth); throttled/server error → transient."""
    entry = _oauth_entry(
        {"access_token": "old", "refresh_token": "r", "expires_at": time.time() - 10}
    )
    entry.add_to_hass(hass)
    aioclient_mock.post(OAUTH2_TOKEN_URL, status=status)

    provider = token_provider(await _session(hass, entry))
    with pytest.raises(exc):
        await provider()


async def test_setup_oauth_entry_uses_bearer_client(hass: HomeAssistant) -> None:
    """An OAuth entry builds the client with a token provider, not an API key."""
    entry = _oauth_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.solaredge_one.SolarEdgeOneClient",
        side_effect=RuntimeError("stop after client creation"),
    ) as mock_cls:
        await hass.config_entries.async_setup(entry.entry_id)
    kwargs = mock_cls.call_args.kwargs
    assert "token_provider" in kwargs
    assert "api_key" not in kwargs


@pytest.mark.parametrize(
    ("frontend_base", "external_url", "expected"),
    [
        # Browsing over HTTPS: use what the user sees.
        ("https://ha.example.com", None, "https://ha.example.com"),
        # Plain-HTTP LAN address but an HTTPS external URL: SolarEdge rejects
        # http/IP redirect URIs, so suggest the HTTPS one.
        ("http://192.168.0.10:8123", "https://ha.example.com", "https://ha.example.com"),
        # Nothing better available: fall back to the browser's address.
        ("http://192.168.0.10:8123", None, "http://192.168.0.10:8123"),
    ],
)
async def test_callback_url_prefers_https(
    hass: HomeAssistant,
    frontend_base: str,
    external_url: str | None,
    expected: str,
) -> None:
    """The suggested redirect URI prefers an HTTPS, non-IP address."""

    await hass.config.async_update(
        external_url=external_url, internal_url="http://192.168.0.10:8123"
    )
    request = MagicMock()
    request.headers = {config_entry_oauth2_flow.HEADER_FRONTEND_BASE: frontend_base}
    with patch(
        "custom_components.solaredge_one.oauth.http.current_request"
    ) as current:
        current.get.return_value = request
        assert callback_url(hass) == f"{expected}{OAUTH_CALLBACK_PATH}"


@pytest.mark.parametrize(
    ("external_url", "expected"),
    [
        ("https://ha.example.com", f"https://ha.example.com{OAUTH_CALLBACK_PATH}"),
        (None, f"https://<your-home-assistant-domain>{OAUTH_CALLBACK_PATH}"),
    ],
)
async def test_credentials_dialog_never_suggests_http_or_ip(
    hass: HomeAssistant, external_url: str | None, expected: str
) -> None:
    """The credentials dialog (no browser address) avoids http/IP redirect URIs."""
    await hass.config.async_update(
        external_url=external_url, internal_url="http://192.168.0.10:8123"
    )
    placeholders = await async_get_description_placeholders(hass)
    assert placeholders["redirect_url"] == expected
