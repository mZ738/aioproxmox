"""Tests for moving to another node of the cluster when the configured host is gone."""

import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from aioproxmox import ProxmoxVE
from aioproxmox.endpoints import ClusterEndpoint


def _client_with_session() -> tuple[ProxmoxVE, AsyncMock]:
    session = AsyncMock(spec=aiohttp.ClientSession)
    login = AsyncMock()
    login.status = 200
    login.json.return_value = {"data": {"ticket": "t2", "CSRFPreventionToken": "c2"}}
    session.post.return_value.__aenter__.return_value = login
    pve = ProxmoxVE(
        session=session, host="192.0.2.1", user="root@pam", password="secret"
    )
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
async def test_failover_moves_to_the_next_host_that_answers():
    """The configured host is gone; the second candidate answers `version`."""
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # the request on host 1
        aiohttp.ClientConnectionError("refused"),  # version probe on host 2
        _response(200, {"version": "9.2"}),  # version probe on host 3
        _response(200, {"ok": 1}),  # the request, repeated on host 3
    ]

    assert await pve.request("GET", "nodes") == {"ok": 1}
    assert pve.host == "192.0.2.3"
    assert pve.base_url == "https://192.0.2.3:8006/api2/json"
    assert pve.auth.base_url == pve.base_url
    urls = [call.kwargs["url"] for call in session.request.call_args_list]
    assert urls[-1] == "https://192.0.2.3:8006/api2/json/nodes"


@pytest.mark.asyncio
async def test_without_another_host_the_failure_is_reported():
    """A single host has nowhere to go; the connection error reaches the caller."""
    pve, session = _client_with_session()
    session.request.return_value.__aenter__.side_effect = aiohttp.ClientConnectionError(
        "refused"
    )

    with pytest.raises(aiohttp.ClientConnectionError):
        await pve.request("GET", "nodes")
    assert pve.hosts == ("192.0.2.1",)


@pytest.mark.asyncio
async def test_learn_hosts_from_cluster_status():
    """Every node's corosync address except the local one is remembered, once."""
    pve, session = _client_with_session()
    session.request.return_value.__aenter__.return_value = _response(
        200,
        [
            {"type": "cluster", "name": "lab", "nodes": 3},
            {"type": "node", "name": "pve1", "ip": "192.0.2.1", "local": 1},
            {"type": "node", "name": "pve2", "ip": "192.0.2.2", "local": 0},
            {"type": "node", "name": "pve3", "ip": "192.0.2.3"},
        ],
    )

    await pve.learn_hosts_from_cluster()
    await pve.learn_hosts_from_cluster()

    assert pve.hosts == ("192.0.2.1", "192.0.2.2", "192.0.2.3")


@pytest.mark.asyncio
async def test_learning_hosts_survives_a_refused_status_read():
    """cluster/status needs no privilege, but a 403 still must not break setup."""
    pve, session = _client_with_session()
    session.request.return_value.__aenter__.return_value = _response(403, text="denied")

    await pve.learn_hosts_from_cluster()

    assert pve.hosts == ("192.0.2.1",)


@pytest.mark.asyncio
async def test_cluster_status_lists_the_nodes():
    """cluster/status: the cluster entry and one entry per node, dicts only."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        return_value=[
            {"type": "cluster", "name": "lab"},
            {"type": "node", "name": "pve1", "ip": "192.0.2.1", "local": 1},
            "nonsense",
        ]
    )
    entries = await ClusterEndpoint(mock_client).status()
    assert [entry["type"] for entry in entries] == ["cluster", "node"]
    mock_client.request.assert_called_with("GET", "cluster/status")
