"""Test basic aiohttp."""

import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from aioproxmox import ProxmoxHTTPApiTokenAuth, ProxmoxHTTPAuth, ProxmoxVE
from aioproxmox.endpoints import AccessEndpoint, ClusterEndpoint, NodeEndpoint
from aioproxmox.exceptions import ProxmoxAPIError, ProxmoxAuthError


@pytest.mark.asyncio
async def test_proxmox_ve_missing_creds():
    """Test missing credentials."""
    session = MagicMock(spec=aiohttp.ClientSession)
    with pytest.raises(ProxmoxAuthError, match="No valid authentication credentials"):
        ProxmoxVE(session=session, host="127.0.0.1")


@pytest.mark.asyncio
async def test_api_token_auth_headers():
    """Test headers from api_token authentication."""
    session = MagicMock(spec=aiohttp.ClientSession)
    pve = ProxmoxVE(
        session=session,
        host="127.0.0.1",
        user="root@pam",
        token_name="test",
        token_value="secret",
    )
    assert isinstance(pve.auth, ProxmoxHTTPApiTokenAuth)
    headers = pve.auth.get_headers()
    assert headers["Authorization"] == "PVEAPIToken=root@pam!test=secret"
    assert pve.auth.get_cookies() == {}


@pytest.mark.asyncio
async def test_request_error_handling():
    """Test request error handling."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 403
    mock_resp.text.return_value = "Permission denied"
    session.request.return_value.__aenter__.return_value = mock_resp

    pve = ProxmoxVE(
        session=session,
        host="127.0.0.1",
        user="root@pam",
        token_name="test",
        token_value="secret",
    )

    with pytest.raises(
        ProxmoxAPIError, match="PVE API Error 403 at nodes: Permission denied"
    ):
        await pve.request("GET", "nodes")


@pytest.mark.asyncio
async def test_http_auth_ticket_success():
    """Test basic username and password authentication via ticket."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = {
        "data": {
            "ticket": "PVE:root@pam:mock_ticket",
            "CSRFPreventionToken": "mock_csrf",
        }
    }
    session.post.return_value.__aenter__.return_value = mock_resp

    auth = ProxmoxHTTPAuth(
        "root@pam", "password", base_url="https://mock", session=session
    )
    await auth.async_init()

    assert auth.pve_auth_ticket == "PVE:root@pam:mock_ticket"
    assert auth.csrf_prevention_token == "mock_csrf"

    cookies = auth.get_cookies()
    assert cookies["PVEAuthCookie"] == "PVE:root@pam:mock_ticket"

    headers = auth.get_headers()
    assert headers["CSRFPreventionToken"] == "mock_csrf"


@pytest.mark.asyncio
async def test_http_auth_ticket_failure():
    """Test failed authentication."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 401
    session.post.return_value.__aenter__.return_value = mock_resp

    auth = ProxmoxHTTPAuth(
        "root@pam", "bad_pass", base_url="https://mock", session=session
    )

    with pytest.raises(ProxmoxAuthError, match="Couldn't authenticate user"):
        await auth.async_init()


@pytest.mark.asyncio
async def test_http_auth_ticket_tfa_success():
    """Test successful Two-Factor Authentication flow."""
    session = AsyncMock(spec=aiohttp.ClientSession)

    # Mock first response: Requires TFA
    mock_resp1 = AsyncMock()
    mock_resp1.status = 200
    mock_resp1.json.return_value = {
        "data": {
            "ticket": "temp_ticket",
            "CSRFPreventionToken": "temp_csrf",
            "NeedTFA": 1,
        }
    }

    # Mock second response: TFA Success
    mock_resp2 = AsyncMock()
    mock_resp2.status = 200
    mock_resp2.json.return_value = {
        "data": {"ticket": "final_ticket", "CSRFPreventionToken": "final_csrf"}
    }

    # Chain the context managers for the two consecutive post requests
    session.post.side_effect = [
        AsyncMock(__aenter__=AsyncMock(return_value=mock_resp1)),
        AsyncMock(__aenter__=AsyncMock(return_value=mock_resp2)),
    ]

    auth = ProxmoxHTTPAuth(
        "root@pam", "password", otp="123456", base_url="https://mock", session=session
    )
    await auth.async_init()

    assert auth.pve_auth_ticket == "final_ticket"
    assert auth.csrf_prevention_token == "final_csrf"


@pytest.mark.asyncio
async def test_http_auth_ticket_tfa_failure():
    """Test failed Two-Factor Authentication."""
    session = AsyncMock(spec=aiohttp.ClientSession)

    mock_resp1 = AsyncMock()
    mock_resp1.status = 200
    mock_resp1.json.return_value = {
        "data": {"ticket": "temp", "CSRFPreventionToken": "temp", "NeedTFA": 1}
    }

    # Missing data in TFA response
    mock_resp2 = AsyncMock()
    mock_resp2.status = 200
    mock_resp2.json.return_value = {}

    session.post.side_effect = [
        AsyncMock(__aenter__=AsyncMock(return_value=mock_resp1)),
        AsyncMock(__aenter__=AsyncMock(return_value=mock_resp2)),
    ]

    auth = ProxmoxHTTPAuth(
        "root@pam", "password", otp="bad_otp", base_url="https://mock", session=session
    )

    with pytest.raises(ProxmoxAuthError, match="missing Two Factor Authentication"):
        await auth.async_init()


@pytest.mark.asyncio
async def test_http_auth_check_and_refresh():
    """Test that tokens are refreshed when birth_time exceeds renew_age."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = {
        "data": {"ticket": "refreshed_ticket", "CSRFPreventionToken": "refreshed_csrf"}
    }
    session.post.return_value.__aenter__.return_value = mock_resp

    auth = ProxmoxHTTPAuth(
        "root@pam", "password", base_url="https://mock", session=session
    )
    auth.birth_time = time.monotonic() - 4000  # Artificially age the token past 3600s
    auth.pve_auth_ticket = "old_ticket"

    await auth.check_and_refresh("GET")

    assert auth.pve_auth_ticket == "refreshed_ticket"


@pytest.mark.asyncio
async def test_pve_connect_and_properties():
    """Test connection initialization and property endpoints."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = {"data": []}
    session.request.return_value.__aenter__.return_value = mock_resp

    pve = ProxmoxVE(
        session=session,
        host="127.0.0.1",
        user="root@pam",
        token_name="t",
        token_value="v",
    )

    # Default port generation test
    assert pve.base_url == "https://127.0.0.1:8006/api2/json"

    # Endpoints
    assert isinstance(pve.cluster, ClusterEndpoint)
    assert isinstance(pve.access, AccessEndpoint)
    assert isinstance(pve.nodes("pve-01"), NodeEndpoint)

    # Connect trigger
    resources = await pve.connect()
    assert resources.resources == []


@pytest.mark.asyncio
async def test_request_query_params_and_csrf():
    """Test GET query parameter handling and POST CSRF handling in the request loop."""
    session = AsyncMock(spec=aiohttp.ClientSession)

    # Mock generic request response
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = {"data": {"success": True}}
    session.request.return_value.__aenter__.return_value = mock_resp

    # Mock auth refresh post response so check_and_refresh succeeds
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.json.return_value = {
        "data": {"ticket": "valid_ticket", "CSRFPreventionToken": "mutation_csrf"}
    }
    session.post.return_value.__aenter__.return_value = mock_auth_resp

    pve = ProxmoxVE(
        session=session, host="127.0.0.1", user="root@pam", password="secret"
    )

    # Ensure token timestamp is fresh
    pve.auth.birth_time = time.monotonic()
    pve.auth.csrf_prevention_token = "mutation_csrf"

    # Test GET boolean and integer param conversion in `params`
    await pve.request("GET", "test/path", params={"isActive": True, "limit": 50})

    call_kwargs = session.request.call_args[1]
    assert call_kwargs["params"] == {"isActive": 1, "limit": "50"}

    # Test POST payload and CSRF header assignment
    await pve.request("POST", "test/path", json_data={"new_val": "data"})
    post_kwargs = session.request.call_args[1]
    assert post_kwargs["json"] == {"new_val": "data"}
    assert post_kwargs["headers"]["CSRFPreventionToken"] == "mutation_csrf"


@pytest.mark.asyncio
async def test_a_command_answers_with_its_task_id():
    """A POST that queues a task answers with the UPID string, not an empty dict."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.return_value = {"data": "UPID:pve:0001:0001:1:qmstart:101:root@pam:"}
    session.request.return_value.__aenter__.return_value = mock_resp

    pve = ProxmoxVE(
        session=session,
        host="127.0.0.1",
        user="root@pam",
        token_name="test",
        token_value="secret",
    )

    assert await pve.request("POST", "nodes/pve/qemu/101/status/start") == (
        "UPID:pve:0001:0001:1:qmstart:101:root@pam:"
    )
    mock_resp.json.return_value = {"data": None}
    assert await pve.request("GET", "nodes/pve/subscription") is None


@pytest.mark.asyncio
async def test_an_api_error_carries_proxmox_reason_phrase():
    """Proxmox explains a refusal in the HTTP reason, with an empty body."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = AsyncMock()
    mock_resp.status = 403
    mock_resp.reason = "Permission check failed (/nodes/pve, Sys.PowerMgmt)"
    mock_resp.text.return_value = '{"data":null}'
    session.request.return_value.__aenter__.return_value = mock_resp

    pve = ProxmoxVE(
        session=session,
        host="127.0.0.1",
        user="root@pam",
        token_name="test",
        token_value="secret",
    )

    with pytest.raises(
        ProxmoxAPIError, match=r"Permission check failed \(/nodes/pve, Sys.PowerMgmt\)"
    ):
        await pve.request("POST", "nodes/pve/status")

    # A body that says more than the reason is kept alongside it.
    mock_resp.status = 400
    mock_resp.reason = "Parameter verification failed."
    mock_resp.text.return_value = '{"data":null,"errors":{"storage":"missing"}}'
    with pytest.raises(
        ProxmoxAPIError, match=r"Parameter verification failed\.: .*storage"
    ):
        await pve.request("POST", "nodes/pve/vzdump")
