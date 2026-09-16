"""Tests for the command paths that Proxmox actually has."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aioproxmox.endpoints import NodeEndpoint


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
