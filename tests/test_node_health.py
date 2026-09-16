"""Tests for a node's certificates, subscription, replication, ports and load."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from aioproxmox.endpoints import NodeEndpoint
from aioproxmox.model.health import (
    Certificate,
    ReplicationHealth,
    ReplicationJob,
    Subscription,
    SubscriptionStatus,
    serving_certificate,
)
from aioproxmox.model.summary import NodeInterface, load_average, node_mac_addresses


def test_the_serving_certificate_is_the_custom_one_when_there_is_one():
    """pveproxy-ssl.pem wins over pve-ssl.pem; the cluster CA is never chosen."""
    certificates = [
        Certificate(filename="pve-root-ca.pem", notafter=2_000_000_000),
        Certificate(
            filename="pve-ssl.pem", notafter=1_800_000_000, issuer="Proxmox CA"
        ),
        Certificate(filename="pveproxy-ssl.pem", notafter=1_760_000_000, issuer="R3"),
    ]
    chosen = serving_certificate(certificates)
    assert chosen is not None
    assert chosen.filename == "pveproxy-ssl.pem"
    assert chosen.expires == datetime.fromtimestamp(1_760_000_000, tz=UTC)

    assert serving_certificate(certificates[:2]).filename == "pve-ssl.pem"
    assert serving_certificate([certificates[0]]) is None
    assert Certificate(filename="x").expires is None


def test_subscription_states_and_the_unknown_fallback():
    """A documented state maps to the enum; anything else is unknown, not a crash."""
    assert (
        Subscription.from_dict({"status": "notfound"}).status
        is SubscriptionStatus.NOTFOUND
    )
    sub = Subscription.from_dict(
        {"status": "active", "level": "c", "productname": "PVE Community", "key": "x"}
    )
    assert sub.status is SubscriptionStatus.ACTIVE
    assert sub.productname == "PVE Community"
    assert (
        Subscription.from_dict({"status": "weird"}).status is SubscriptionStatus.UNKNOWN
    )
    assert Subscription.from_dict({}).status is SubscriptionStatus.UNKNOWN


def test_replication_health_reads_the_oldest_sync_and_the_failing_jobs():
    """Disabled jobs count but never alarm; the oldest active sync is the figure."""
    jobs = [
        ReplicationJob(id="100-0", guest=100, target="pve2", last_sync=1_757_900_000),
        ReplicationJob(
            id="101-0",
            guest=101,
            target="pve2",
            last_sync=1_757_000_000,
            fail_count=3,
            error="ssh",
        ),
        ReplicationJob(id="102-0", guest=102, target="pve3", disable=1, fail_count=9),
    ]
    health = ReplicationHealth.from_jobs(jobs)

    assert health.jobs == 3
    assert health.failing is True
    assert [job.id for job in health.failing_jobs] == ["101-0"]
    assert health.oldest_sync == datetime.fromtimestamp(1_757_000_000, tz=UTC)
    assert ReplicationHealth.from_jobs([]).oldest_sync is None
    assert not ReplicationHealth.from_jobs([jobs[2]]).failing


def test_node_mac_addresses_from_the_altnames():
    """A physical port's MAC-based altname decodes to the colon form; bridges have none."""
    interfaces = [
        NodeInterface(iface="vmbr0", type="bridge"),
        NodeInterface(
            iface="eno1", type="eth", altnames=["enp0s31f6", "enx0200a0010203"]
        ),
        NodeInterface(iface="eno2", type="eth", altnames=["enp2s0"]),
        NodeInterface(iface="eno3", type="eth", altnames=["enx0200a0010203"]),
    ]
    assert node_mac_addresses(interfaces) == ["02:00:a0:01:02:03"]
    assert interfaces[0].mac is None
    assert interfaces[2].mac is None


def test_load_average_is_three_numbers_or_nothing():
    """Proxmox reports the load as three strings."""
    assert load_average(["0.5", "1.25", "2"]) == (0.5, 1.25, 2.0)
    assert load_average(["a", "b", "c"]) is None
    assert load_average(["1", "2"]) is None
    assert load_average(None) is None


@pytest.mark.asyncio
async def test_node_health_endpoints():
    """certificates, subscription, replication and network read their paths."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        side_effect=[
            [{"filename": "pve-ssl.pem", "notafter": 1_800_000_000}],
            {"status": "notfound"},
            [{"id": "100-0", "guest": 100}],
            [{"iface": "eno1", "type": "eth", "altnames": ["enx0200a0010203"]}],
        ]
    )
    node = NodeEndpoint(mock_client, "pve-01")

    assert (await node.certificates())[0].filename == "pve-ssl.pem"
    assert (await node.subscription()).status is SubscriptionStatus.NOTFOUND
    assert (await node.replication())[0].guest == 100
    assert (await node.network())[0].mac == "02:00:a0:01:02:03"
    paths = [call.args[1] for call in mock_client.request.call_args_list]
    assert paths == [
        "nodes/pve-01/certificates/info",
        "nodes/pve-01/subscription",
        "nodes/pve-01/replication",
        "nodes/pve-01/network",
    ]
