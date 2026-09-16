"""Tests for the commands beyond power: backups, Wake on LAN, hibernate, unlock, container states."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aioproxmox.endpoints import NodeEndpoint
from aioproxmox.exceptions import ProxmoxError


def _client() -> MagicMock:
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        return_value="UPID:pve:0001:0001:1:vzdump:100:root@pam:"
    )
    return mock_client


@pytest.mark.asyncio
async def test_vzdump_for_named_guests_and_for_everything():
    """Guest ids become the comma list vzdump wants; `all` takes everything."""
    client = _client()
    node = NodeEndpoint(client, "pve-01")

    upid = await node.vzdump(
        [101, 100], storage="nas", mode="snapshot", compress="zstd"
    )
    assert upid.startswith("UPID:")
    client.request.assert_called_with(
        "POST",
        "nodes/pve-01/vzdump",
        json_data={
            "vmid": "101,100",
            "storage": "nas",
            "mode": "snapshot",
            "compress": "zstd",
        },
    )

    await node.vzdump(100)
    assert client.request.call_args.kwargs["json_data"] == {"vmid": "100"}

    await node.vzdump(all_guests=True, storage="nas", notes_template="{{guestname}}")
    assert client.request.call_args.kwargs["json_data"] == {
        "all": 1,
        "storage": "nas",
        "notes-template": "{{guestname}}",
    }


@pytest.mark.asyncio
async def test_vzdump_refuses_nothing_and_notes_without_a_storage():
    """Naming nothing is a mistake, not a backup of nothing; notes need a storage."""
    client = _client()
    node = NodeEndpoint(client, "pve-01")

    with pytest.raises(ProxmoxError):
        await node.vzdump()
    with pytest.raises(ProxmoxError):
        await node.vzdump([])
    with pytest.raises(ProxmoxError):
        await node.vzdump(100, notes_template="{{guestname}}")
    client.request.assert_not_called()


@pytest.mark.asyncio
async def test_wake_on_lan_and_the_container_states():
    """The paths of the actions added: node WOL, LXC shutdown/suspend/resume/reboot, QEMU reboot."""
    client = _client()
    node = NodeEndpoint(client, "pve-01")

    await node.wakeonlan()
    client.request.assert_called_with("POST", "nodes/pve-01/wakeonlan", json_data={})

    for action, path in (
        (node.lxc(100).status.shutdown, "nodes/pve-01/lxc/100/status/shutdown"),
        (node.lxc(100).status.suspend, "nodes/pve-01/lxc/100/status/suspend"),
        (node.lxc(100).status.resume, "nodes/pve-01/lxc/100/status/resume"),
        (node.lxc(100).status.reboot, "nodes/pve-01/lxc/100/status/reboot"),
        (node.qemu(101).status.reboot, "nodes/pve-01/qemu/101/status/reboot"),
    ):
        await action()
        client.request.assert_called_with("POST", path, json_data={})


@pytest.mark.asyncio
async def test_hibernate_and_unlock():
    """Hibernate is suspend-to-disk; unlock deletes the config lock, skiplock for QEMU only."""
    client = _client()
    node = NodeEndpoint(client, "pve-01")

    await node.qemu(101).status.hibernate()
    client.request.assert_called_with(
        "POST", "nodes/pve-01/qemu/101/status/suspend", json_data={"todisk": 1}
    )
    await node.qemu(101).status.unlock()
    client.request.assert_called_with(
        "PUT",
        "nodes/pve-01/qemu/101/config",
        json_data={"delete": "lock", "skiplock": 1},
    )
    await node.lxc(100).status.unlock()
    client.request.assert_called_with(
        "PUT", "nodes/pve-01/lxc/100/config", json_data={"delete": "lock"}
    )
