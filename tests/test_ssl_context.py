"""Tests for verifying against a private CA through an SSL context."""

import ssl
import time
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aioproxmox import ProxmoxVE


def _response(status: int, payload: object = None) -> AsyncMock:
    response = AsyncMock()
    response.status = status
    response.json.return_value = {"data": payload}
    response.text.return_value = ""
    return response


@pytest.mark.asyncio
async def test_an_ssl_context_reaches_every_request_and_the_login():
    """A context stands in for the bool: aiohttp gets it as `ssl=` on login and request."""
    context = ssl.create_default_context()
    session = AsyncMock(spec=aiohttp.ClientSession)
    login = AsyncMock()
    login.status = 200
    login.json.return_value = {"data": {"ticket": "t1", "CSRFPreventionToken": "c1"}}
    session.post.return_value.__aenter__.return_value = login
    session.request.return_value.__aenter__.return_value = _response(200, {"ok": 1})

    pve = ProxmoxVE(
        session=session,
        host="192.0.2.1",
        user="root@pam",
        password="secret",
        verify_ssl=context,
    )
    await pve.auth.async_init()
    assert await pve.request("GET", "version") == {"ok": 1}

    assert session.post.call_args.kwargs["ssl"] is context
    assert session.request.call_args.kwargs["ssl"] is context


@pytest.mark.asyncio
async def test_a_token_client_takes_the_context_too():
    """Token authentication has no login, but its requests verify the same way."""
    context = ssl.create_default_context()
    session = AsyncMock(spec=aiohttp.ClientSession)
    session.request.return_value.__aenter__.return_value = _response(200, [])

    pve = ProxmoxVE(
        session=session,
        host="192.0.2.1",
        user="root@pam",
        token_name="ha",
        token_value="secret",
        verify_ssl=context,
    )
    pve.auth.birth_time = time.monotonic()
    await pve.request("GET", "nodes")

    assert session.request.call_args.kwargs["ssl"] is context
