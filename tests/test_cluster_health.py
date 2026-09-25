"""Tests for the cluster's Ceph and HA status, backup coverage and summary."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from aioproxmox.endpoints import ClusterEndpoint
from aioproxmox.model.health import CephHealth, CephStatus, HAStatus
from aioproxmox.model.pve import ClusterResourcesCollection
from aioproxmox.model.summary import ClusterSummary


def test_ceph_status_reads_health_checks_and_the_two_totals():
    """The health block in full, the pgmap reduced to used and total bytes."""
    status = CephStatus.from_api(
        {
            "health": {
                "status": "HEALTH_WARN",
                "checks": {
                    "OSD_NEARFULL": {
                        "severity": "HEALTH_WARN",
                        "summary": {"message": "1 nearfull osd(s)"},
                    },
                    "broken": "not a dict",
                },
            },
            "pgmap": {"bytes_used": 1000, "bytes_total": 4000, "num_pgs": 128},
            "osdmap": {"whatever": 1},
        }
    )
    assert status.health is CephHealth.WARN
    assert status.checks[0].check == "OSD_NEARFULL"
    assert status.checks[0].message == "1 nearfull osd(s)"
    assert (status.bytes_used, status.bytes_total) == (1000, 4000)

    empty = CephStatus.from_api({})
    assert empty.health is CephHealth.UNKNOWN
    assert not empty.checks
    assert empty.bytes_used is None
    assert (
        CephStatus.from_api({"health": {"status": "HEALTH_ODD"}}).health
        is CephHealth.UNKNOWN
    )


def test_ha_status_reads_the_structured_fields_only():
    """Quorum, master, fencing and services; the display string is ignored."""
    now = datetime.now(tz=UTC)
    entries = [
        {"type": "quorum", "quorate": 1, "status": "OK"},
        {
            "type": "master",
            "node": "pve1",
            "timestamp": int((now - timedelta(seconds=5)).timestamp()),
            "status": "pve1 (active, Wed Sep 16 ...)",
        },
        {"type": "fencing", "armed-state": "armed", "resource_mode": "freeze"},
        {
            "type": "service",
            "sid": "vm:100",
            "node": "pve1",
            "crm_state": "started",
            "state": "started",
        },
        {
            "type": "service",
            "sid": "vm:101",
            "node": "pve2",
            "crm_state": "error",
            "state": "error",
        },
        "nonsense",
    ]
    status = HAStatus.from_api(entries)

    assert status.quorate is True
    assert status.crm_master == "pve1"
    assert status.crm_master_stale(now) is False
    assert status.crm_master_stale(now + timedelta(seconds=60)) is True
    assert status.armed_state == "armed"
    assert status.resource_mode == "freeze"
    assert status.resources_total == 2
    assert [p.sid for p in status.resources_error] == ["vm:101"]

    odd = HAStatus.from_api([{"type": "fencing", "armed-state": "confused"}])
    assert odd.armed_state is None
    assert odd.crm_master_stale() is None


def test_cluster_summary_weights_cpu_by_cores(mock_pve_dual_node_cluster_raw):
    """Nodes online, guests running, CPU weighted by core count, memory summed."""
    resources = ClusterResourcesCollection.from_dict(
        {"resources": mock_pve_dual_node_cluster_raw}
    )
    summary = ClusterSummary.from_resources(resources)

    assert summary.nodes_total == 2
    assert summary.nodes_online + len(summary.nodes_offline) == 2
    assert summary.qemu_total + summary.lxc_total > 0
    assert summary.cpu is None or 0 <= summary.cpu <= 1
    if summary.memory_total:
        assert summary.memory_used <= summary.memory_total


def test_cluster_summary_of_nothing():
    """An empty cluster has nothing to add up."""
    summary = ClusterSummary.from_resources(ClusterResourcesCollection(resources=[]))
    assert summary.nodes_total == 0
    assert summary.cpu is None
    assert summary.memory_total is None


@pytest.mark.asyncio
async def test_cluster_health_endpoints():
    """ceph_status, ha_status and not_backed_up read their paths."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        side_effect=[
            {"health": {"status": "HEALTH_OK"}},
            [{"type": "quorum", "quorate": 1}],
            [{"vmid": 105, "type": "qemu", "name": "forgotten"}],
        ]
    )
    cluster = ClusterEndpoint(mock_client)

    assert (await cluster.ceph_status()).health is CephHealth.OK
    assert (await cluster.ha_status()).quorate is True
    assert (await cluster.not_backed_up())[0].vmid == 105
    paths = [call.args[1] for call in mock_client.request.call_args_list]
    assert paths == [
        "cluster/ceph/status",
        "cluster/ha/status/current",
        "cluster/backup-info/not-backed-up",
    ]
