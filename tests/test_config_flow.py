"""Config flow tests for SolarEdge ONE."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiosolaredge_one import Site, SolarEdgeAuthError, SolarEdgeConnectionError
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaredge_one.const import (
    CONF_API_KEY,
    CONF_PLAN_TYPE,
    CONF_SITE_ID,
    DOMAIN,
    PLAN_FLEET,
    PLAN_SITE_OWNER,
)


def _site(site_id: int, name: str = "My Home", status: str = "ACTIVE") -> Site:
    return Site(site_id=site_id, name=name, activation_status=status)


def _patch_client(sites=None, side_effect=None):
    """Patch the client used inside the config flow."""
    patcher = patch("custom_components.solaredge_one.config_flow.SolarEdgeOneClient")
    mock_cls = patcher.start()
    client = mock_cls.return_value
    client.get_sites = AsyncMock(return_value=sites or [], side_effect=side_effect)
    return patcher, client


async def test_user_flow_single_site(hass: HomeAssistant) -> None:
    """A token with one site creates an entry directly (no site picker)."""
    patcher, _ = _patch_client(sites=[_site(3066774, "My Home")])
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PLAN_TYPE: PLAN_SITE_OWNER, CONF_API_KEY: "test-key"},
        )
    finally:
        patcher.stop()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Home"
    assert result["data"][CONF_SITE_ID] == 3066774
    assert result["result"].unique_id == "3066774"


async def test_user_flow_multi_site(hass: HomeAssistant) -> None:
    """A fleet token with several sites shows the site picker."""
    patcher, _ = _patch_client(sites=[_site(1, "Alpha"), _site(2, "Beta")])
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PLAN_TYPE: PLAN_FLEET, CONF_API_KEY: "test-key"},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_site"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SITE_ID: "2"}
        )
    finally:
        patcher.stop()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Beta"
    assert result["data"][CONF_SITE_ID] == 2
    assert result["result"].unique_id == "2"


@pytest.mark.parametrize(
    ("side_effect", "expected_error"),
    [
        (SolarEdgeAuthError("bad"), "invalid_auth"),
        (SolarEdgeConnectionError("down"), "cannot_connect"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, side_effect: Exception, expected_error: str
) -> None:
    """Auth/connection failures surface as form errors, not crashes."""
    patcher, _ = _patch_client(side_effect=side_effect)
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PLAN_TYPE: PLAN_SITE_OWNER, CONF_API_KEY: "bad-key"},
        )
    finally:
        patcher.stop()

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}


async def test_user_flow_no_new_sites(hass: HomeAssistant) -> None:
    """If every site is already configured, the flow aborts cleanly."""
    MockConfigEntry(domain=DOMAIN, unique_id="1", data={}).add_to_hass(hass)
    patcher, _ = _patch_client(sites=[_site(1, "Alpha")])
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PLAN_TYPE: PLAN_FLEET, CONF_API_KEY: "test-key"},
        )
    finally:
        patcher.stop()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_sites"


async def test_duplicate_site_aborts(hass: HomeAssistant) -> None:
    """Selecting an already-configured site aborts as already_configured."""
    MockConfigEntry(domain=DOMAIN, unique_id="3066774", data={}).add_to_hass(hass)
    # Only one site and it's already configured -> filtered out -> no_sites.
    patcher, _ = _patch_client(sites=[_site(3066774, "My Home")])
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PLAN_TYPE: PLAN_SITE_OWNER, CONF_API_KEY: "test-key"},
        )
    finally:
        patcher.stop()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_sites"
