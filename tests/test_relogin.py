"""Tests for logging in again once a ticket has died."""

import time
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aioproxmox import ProxmoxHTTPAuth, ProxmoxVE
from aioproxmox.exceptions import ProxmoxAPIError, ProxmoxAuthError


def _client_with_session() -> tuple[ProxmoxVE, AsyncMock]:
    session = AsyncMock(spec=aiohttp.ClientSession)
    login = AsyncMock()
    login.status = 200
    login.json.return_value = {"data": {"ticket": "t2", "CSRFPreventionToken": "c2"}}
    session.post.return_value.__aenter__.return_value = login
    pve = ProxmoxVE(
        session=session, host="192.0.2.1", user="root@pam", password="secret"
    )
    assert isinstance(pve.auth, ProxmoxHTTPAuth)
    pve.auth.birth_time = time.monotonic()
    pve.auth.pve_auth_ticket = "t1"
    return pve, session


def _response(status: int, payload: object = None, text: str = "") -> AsyncMock:
    response = AsyncMock()
    response.status = status
    response.json.return_value = {"data": payload}
    response.text.return_value = text
    return response


@pytest.mark.asyncio
async def test_a_401_mid_flight_logs_in_again_and_repeats_once():
    """The ticket died while the host was away; the password is still here."""
    pve, session = _client_with_session()
    session.request.return_value.__aenter__.side_effect = [
        _response(401, text="ticket expired"),
        _response(200, {"ok": 1}),
    ]

    assert await pve.request("GET", "version") == {"ok": 1}
    assert pve.auth.pve_auth_ticket == "t2"
    assert session.post.call_count == 1


@pytest.mark.asyncio
async def test_a_refused_renewal_falls_back_to_the_password():
    """check_and_refresh: renewal with the ticket refused, login with the password works."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    refused = AsyncMock()
    refused.status = 401
    granted = AsyncMock()
    granted.status = 200
    granted.json.return_value = {
        "data": {"ticket": "fresh", "CSRFPreventionToken": "c"}
    }
    session.post.return_value.__aenter__.side_effect = [refused, granted]

    auth = ProxmoxHTTPAuth(
        "root@pam", "secret", base_url="https://mock", session=session
    )
    auth.birth_time = time.monotonic() - 4000
    auth.pve_auth_ticket = "old"

    await auth.check_and_refresh("GET")

    assert auth.pve_auth_ticket == "fresh"
    # Second call used the password, not the old ticket.
    assert session.post.call_args_list[1].kwargs["data"]["password"] == "secret"


@pytest.mark.asyncio
async def test_a_wrong_password_still_raises():
    """A 401 that a fresh login does not cure is an authentication error."""
    pve, session = _client_with_session()
    refused = AsyncMock()
    refused.status = 401
    session.post.return_value.__aenter__.return_value = refused
    session.request.return_value.__aenter__.return_value = _response(401, text="no")

    with pytest.raises(ProxmoxAuthError):
        await pve.request("GET", "version")


@pytest.mark.asyncio
async def test_other_api_errors_are_not_retried():
    """A 403 is a 403; neither relogin nor failover applies."""
    pve, session = _client_with_session()
    session.request.return_value.__aenter__.return_value = _response(403, text="denied")

    with pytest.raises(ProxmoxAPIError):
        await pve.request("GET", "nodes")
    assert session.request.call_count == 1
