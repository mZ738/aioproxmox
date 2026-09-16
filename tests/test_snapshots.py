"""Tests for the snapshot lists of VMs and containers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aioproxmox.endpoints import NodeEndpoint
from aioproxmox.model.guest import Snapshot, snapshots_taken


@pytest.mark.asyncio
async def test_snapshot_lists_for_vm_and_container(mock_snapshots):
    """`current` is listed but is no snapshot; the rest sort newest first."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=mock_snapshots)
    node = NodeEndpoint(mock_client, "pve-01")

    entries = await node.qemu(101).snapshots()
    mock_client.request.assert_called_with("GET", "nodes/pve-01/qemu/101/snapshot")
    taken = snapshots_taken(entries)
    assert [s.name for s in taken] == ["before-update", "clean-install"]
    assert taken[0].taken_at.timestamp() == 1_757_900_000
    assert entries[-1].is_current
    assert entries[-1].taken_at is None

    await node.lxc(100).snapshots()
    mock_client.request.assert_called_with("GET", "nodes/pve-01/lxc/100/snapshot")


def test_only_current_means_no_snapshots():
    """A guest without snapshots lists just the live state."""
    assert snapshots_taken([Snapshot(name="current", running=1)]) == []
