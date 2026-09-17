"""Tests for the guest agent's filesystems and addresses, and a container's interfaces."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aioproxmox.endpoints import NodeEndpoint
from aioproxmox.model.guest import (
    GuestFilesystem,
    GuestInterface,
    guest_addresses,
    guest_disk_usage,
    primary_address,
)


@pytest.mark.asyncio
async def test_agent_fsinfo_and_disk_usage(mock_agent_fsinfo):
    """The agent's `result` envelope is unwrapped; usage counts a device once."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=mock_agent_fsinfo)
    agent = NodeEndpoint(mock_client, "pve-01").qemu(101).agent

    filesystems = await agent.fsinfo()
    mock_client.request.assert_called_with(
        "GET", "nodes/pve-01/qemu/101/agent/get-fsinfo"
    )
    assert [f.name for f in filesystems] == ["sda1", "sda1", "loop0"]
    used, total = guest_disk_usage(filesystems)
    # sda1 is mounted twice and counts once; loop0 has no disk entry.
    assert used == 12_000_000_000
    assert total == 33_000_000_000


def test_disk_usage_total_only_when_every_filesystem_has_one():
    """A used figure without a matching total leaves the total unknown."""
    filesystems = [
        GuestFilesystem(
            name="a", used_bytes=10, total_bytes=100, disk=[{"dev": "/dev/a"}]
        ),
        GuestFilesystem(name="b", used_bytes=5, disk=[{"dev": "/dev/b"}]),
    ]
    assert guest_disk_usage(filesystems) == (15, None)
    assert guest_disk_usage([]) == (None, None)


@pytest.mark.asyncio
async def test_agent_network_interfaces_and_addresses(mock_agent_interfaces):
    """Loopback and link-local are left out; IPv4 wins for the primary address."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=mock_agent_interfaces)
    agent = NodeEndpoint(mock_client, "pve-01").qemu(101).agent

    interfaces = await agent.network_interfaces()
    assert [i.name for i in interfaces] == ["lo", "ens18"]
    assert interfaces[1].mac == "02:00:0a:01:02:03"
    assert guest_addresses(interfaces) == {"ens18": ["192.0.2.10", "2001:db8::10"]}
    assert primary_address(interfaces) == "192.0.2.10"


@pytest.mark.asyncio
async def test_container_interfaces_use_inet_strings(mock_lxc_interfaces):
    """A container lists `inet`/`inet6` with prefix lengths; the prefix is dropped."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=mock_lxc_interfaces)
    lxc = NodeEndpoint(mock_client, "pve-01").lxc(100)

    interfaces = await lxc.interfaces()
    mock_client.request.assert_called_with("GET", "nodes/pve-01/lxc/100/interfaces")
    assert interfaces[1].mac == "02:00:0a:01:02:04"
    assert guest_addresses(interfaces) == {"eth0": ["192.0.2.20"]}
    assert primary_address(interfaces) == "192.0.2.20"


def test_a_v6_only_guest_still_has_a_primary_address():
    """No IPv4 at all: the first IPv6 is shown."""
    interfaces = [GuestInterface(name="eth0", inet6="2001:db8::20/64")]
    assert primary_address(interfaces) == "2001:db8::20"
    assert primary_address([GuestInterface(name="lo", inet="127.0.0.1/8")]) is None
