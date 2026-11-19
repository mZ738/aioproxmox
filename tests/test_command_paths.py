"""Tests for the command paths Proxmox answers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aioproxmox.endpoints import NodeEndpoint


def _node() -> tuple[NodeEndpoint, MagicMock]:
    """Return a node endpoint over a client that records its requests."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value="UPID:pve-01:0001:0001:1:x::root@pam:")
    return NodeEndpoint(mock_client, "pve-01"), mock_client


@pytest.mark.asyncio
async def test_a_node_is_rebooted_through_its_status_endpoint():
    """A node takes `POST nodes/{node}/status` with a command; there is no `reboot` path."""
    node, mock_client = _node()

    await node.reboot()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/status", json_data={"command": "reboot"}
    )

    await node.shutdown()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/status", json_data={"command": "shutdown"}
    )


@pytest.mark.asyncio
async def test_the_bulk_node_actions_keep_their_own_paths():
    """`startall` and friends are their own endpoints, unlike reboot and shutdown."""
    node, mock_client = _node()

    await node.startall()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/startall", json_data={}
    )


@pytest.mark.asyncio
async def test_a_guest_is_rebooted_not_restarted():
    """Proxmox knows `reboot` for a guest; `restart` reaches the command it meant."""
    node, mock_client = _node()

    await node.qemu(101).status.reboot()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/qemu/101/status/reboot", json_data={}
    )
    await node.qemu(101).status.restart()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/qemu/101/status/reboot", json_data={}
    )

    await node.lxc(100).status.reboot()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/lxc/100/status/reboot", json_data={}
    )
    await node.lxc(100).status.restart()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/lxc/100/status/reboot", json_data={}
    )


@pytest.mark.asyncio
async def test_a_guest_restart_reaches_reboot():
    """Proxmox knows `reboot`, not `restart`; the old attribute reaches the right command."""
    client = MagicMock()
    client.request = AsyncMock(return_value="UPID:x")
    node = NodeEndpoint(client, "pve-01")

    await node.qemu(101).status.restart()
    client.request.assert_called_with(
        "POST", "nodes/pve-01/qemu/101/status/reboot", json_data={}
    )
    await node.lxc(100).status.restart()
    client.request.assert_called_with(
        "POST", "nodes/pve-01/lxc/100/status/reboot", json_data={}
    )
