"""Tests for what a request hands back, and what an error says."""

from unittest.mock import AsyncMock

import aiohttp
import pytest

from aioproxmox import ProxmoxVE
from aioproxmox.exceptions import ProxmoxAPIError, ProxmoxNodeUnreachableError


def _client(response: AsyncMock) -> ProxmoxVE:
    """Return a token client whose one request answers with `response`."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    session.request.return_value.__aenter__.return_value = response
    return ProxmoxVE(
        session=session,
        host="127.0.0.1",
        user="root@pam",
        token_name="test",
        token_value="secret",
    )


def _response(
    status: int, payload: object = None, text: str = "", reason: str = ""
) -> AsyncMock:
    response = AsyncMock()
    response.status = status
    response.reason = reason
    response.json.return_value = {"data": payload}
    response.text.return_value = text
    return response


@pytest.mark.asyncio
async def test_a_command_hands_back_its_task_id():
    """A command answers with a UPID string, which the caller needs to follow the task."""
    upid = "UPID:pve-01:00001234:0000ABCD:69554B80:qmstart:101:root@pam:"
    pve = _client(_response(200, upid))

    assert await pve.request("POST", "nodes/pve-01/qemu/101/status/start") == upid


@pytest.mark.asyncio
async def test_a_read_hands_back_its_object_or_list():
    """The shapes that worked before keep working."""
    pve = _client(_response(200, {"status": "online"}))
    assert await pve.request("GET", "nodes/pve-01/status") == {"status": "online"}

    pve = _client(_response(200, [{"node": "pve-01"}]))
    assert await pve.request("GET", "nodes") == [{"node": "pve-01"}]


@pytest.mark.asyncio
async def test_an_answer_without_data_is_none():
    """`{"data":null}` is what Proxmox sends for a command that returns nothing."""
    pve = _client(_response(200, None))

    assert await pve.request("PUT", "nodes/pve-01/lxc/100/config") is None


@pytest.mark.asyncio
async def test_an_error_keeps_the_reason_proxmox_gives():
    """The privilege that is missing is in the reason phrase, not in the body."""
    pve = _client(
        _response(
            403,
            text='{"data":null}',
            reason="Permission check failed (/nodes/pve-01, Sys.PowerMgmt)",
        )
    )

    with pytest.raises(ProxmoxAPIError, match="Sys.PowerMgmt"):
        await pve.request("POST", "nodes/pve-01/status")


@pytest.mark.asyncio
async def test_an_error_keeps_a_body_that_says_more():
    """Where the body carries the message, it is kept - with the reason in front of it."""
    pve = _client(
        _response(500, text="VM 101 is locked (backup)", reason="Internal Server Error")
    )

    with pytest.raises(
        ProxmoxAPIError, match="Internal Server Error: VM 101 is locked"
    ):
        await pve.request("POST", "nodes/pve-01/qemu/101/status/start")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "reason"),
    [(595, "No route to host"), (596, "Connection timed out")],
)
async def test_an_unreachable_node_has_its_own_error(status: int, reason: str):
    """The node pveproxy proxied the request to could not be reached."""
    pve = _client(_response(status, text="", reason=reason))

    with pytest.raises(ProxmoxNodeUnreachableError, match=reason) as err:
        await pve.request("GET", "nodes/pve-02/qemu")

    assert isinstance(err.value, ProxmoxAPIError)
    assert err.value.status == status
    assert err.value.endpoint == "nodes/pve-02/qemu"
