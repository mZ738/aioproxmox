"""Tests for Proxmox endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aioproxmox.endpoints import (
    AccessEndpoint,
    ClusterEndpoint,
    LXCStatusEndpoint,
    NodeEndpoint,
    QemuStatusEndpoint,
)
from aioproxmox.exceptions import ProxmoxAPIError, ResourceNotFoundError
from aioproxmox.model.pve import (
    AptArchType,
    ClusterCache,
    ClusterQemuResource,
    ClusterResourcesCollection,
    OperationalStatus,
    QemuStatus,
    ResourceType,
)


@pytest.mark.asyncio
async def test_unimplemented_stubs():
    """Hit the NotImplementedError branches for coverage."""
    mock_client = MagicMock()

    qemu_endpoint = NodeEndpoint(mock_client, "pve-01").qemu(101)
    with pytest.raises(NotImplementedError):
        await qemu_endpoint.status.status()

    lxc_endpoint = NodeEndpoint(mock_client, "pve-01").lxc(202)
    with pytest.raises(NotImplementedError):
        await lxc_endpoint.status.status()


@pytest.mark.asyncio
async def test_cluster_endpoint_resources(mock_cluster_resources):
    """Test ClusterEndpoint fetches and updates cache properly."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=mock_cluster_resources)
    mock_client.cluster_cache = ClusterCache()

    endpoint = ClusterEndpoint(mock_client)
    collection = await endpoint.resources()

    assert len(collection.resources) == 12
    mock_client.request.assert_called_with("GET", "cluster/resources")


@pytest.mark.asyncio
async def test_node_endpoint_actions():
    """Test dynamically generated node actions (PostAction)."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock()

    node_endpoint = NodeEndpoint(mock_client, "pve-01")

    # Call the property directly to invoke the PostAction
    await node_endpoint.reboot()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/status", json_data={"command": "reboot"}
    )
    await node_endpoint.shutdown()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/status", json_data={"command": "shutdown"}
    )

    await node_endpoint.startall()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/startall", json_data={}
    )


@pytest.mark.asyncio
async def test_qemu_endpoint_actions():
    """Test dynamically generated VM actions."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock()

    node_endpoint = NodeEndpoint(mock_client, "pve-01")
    qemu = node_endpoint.qemu(101)

    await qemu.status.start()
    mock_client.request.assert_called_with(
        "POST", "nodes/pve-01/qemu/101/status/start", json_data={}
    )


@pytest.mark.asyncio
async def test_agent_ping():
    """Test Qemu Agent Ping."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock()

    node_endpoint = NodeEndpoint(mock_client, "pve-01")
    agent = node_endpoint.qemu(101).agent

    result = await agent.ping()
    assert result is True
    mock_client.request.assert_called_with("POST", "nodes/pve-01/qemu/101/agent/ping")

    # Simulate failure
    mock_client.request.side_effect = ProxmoxAPIError("status", "message", "endpoint")
    assert await agent.ping() is False


@pytest.mark.asyncio
async def test_access_endpoint_permissions():
    """Test fetching and mapping ACL permissions."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value={"/vms/101": {"VM.PowerMgmt": 1}})

    endpoint = AccessEndpoint(mock_client)
    perms = await endpoint.permissions()

    assert perms.has_vm_permission(101, "VM.PowerMgmt") is True


@pytest.mark.asyncio
async def test_tasks_endpoint_filters(mock_cluster_backup_tasks):
    """Test ClusterEndpoint fetches and updates cache properly."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=mock_cluster_backup_tasks)

    node = NodeEndpoint(mock_client, "pve-01")
    tasks = await node.tasks(typefilter="vzdump", limit=5)

    assert len(tasks.tasks) == 5
    mock_client.request.assert_called_with(
        "GET", "nodes/pve-01/tasks", params={"typefilter": "vzdump", "limit": 5}
    )


@pytest.mark.asyncio
async def test_node_endpoint_status(mock_cluster_node_status):
    """Test node-level status fetchers."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=mock_cluster_node_status)

    endpoint = NodeEndpoint(mock_client, "pve-01")

    status = await endpoint.status()
    assert status.uptime == 86400


@pytest.mark.asyncio
async def test_node_endpoint_storage(mock_cluster_node_storage):
    """Test node-level storage fetchers."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=mock_cluster_node_storage)

    endpoint = NodeEndpoint(mock_client, "pve-01")

    storage = await endpoint.storage()
    assert storage.storages[0].storage == "local"
    assert storage.storages[0].used_fraction > 0

    assert storage.storages[1].storage == "pbs"
    assert storage.storages[1].used_fraction > 0
    assert storage.storages[1].avail > 0


@pytest.mark.asyncio
async def test_node_endpoint_storage_offline(mock_cluster_node_storage_offline):
    """Test node-level storage fetchers."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=mock_cluster_node_storage_offline)

    endpoint = NodeEndpoint(mock_client, "pve-01")

    storage = await endpoint.storage()
    assert storage.storages[0].storage == "local"
    assert storage.storages[0].active == 1
    assert storage.storages[0].used_fraction > 0

    assert storage.storages[1].storage == "pbs"
    assert storage.storages[1].active == 0
    assert storage.storages[1].avail == 0


@pytest.mark.asyncio
async def test_lxc_qemu_current_fallback_logic():
    """Test the VM/LXC telemetry fallback if missing from cache."""
    mock_client = MagicMock()
    # Mocking a cluster resources response that contains our missing LXC
    mock_client.cluster.resources = AsyncMock()

    # Mock the direct request to lxc/qemu current
    mock_client.request = AsyncMock(
        return_value={
            "vmid": 202,
            "status": "running",
            "name": "test",
            "cpus": 1,
            "cpu": 0.5,
            "mem": 1,
            "maxmem": 1,
            "swap": 0,
            "maxswap": 0,
            "disk": 1,
            "maxdisk": 1,
            "diskread": 0,
            "diskwrite": 0,
            "netin": 0,
            "netout": 0,
            "uptime": 1,
        }
    )

    # Start with empty cache so pve_find_node_in_cache returns None initially
    mock_client.cluster_resources = None

    endpoint = NodeEndpoint(mock_client, "unknown").lxc(202)

    # We patch the helper so it fails the first time, but succeeds after cluster.resources() is called
    with patch(
        "aioproxmox.endpoints.pve_find_node_in_cache", side_effect=[None, "pve-02"]
    ):
        status = await endpoint.status.current()
        assert status.vmid == 202
        mock_client.cluster.resources.assert_called_once()
        mock_client.request.assert_called_with(
            "GET", "nodes/pve-02/lxc/202/status/current"
        )


@pytest.mark.asyncio
async def test_qemu_current_with_fallback():
    """Test Qemu current fetch handles cache misses."""
    client = MagicMock()
    client.cluster_resources = None  # Force cache miss

    # Mock the cluster.resources() fallback to "find" the node
    async def mock_cluster_resources():
        client.cluster_resources = ClusterResourcesCollection(
            resources=[
                ClusterQemuResource(
                    id="qemu/101",
                    vmid=101,
                    name="vm",
                    node="pve-02",
                    status=OperationalStatus.RUNNING,
                    template=0,
                    maxcpu=1,
                    maxmem=1,
                    maxdisk=1,
                    resource_type=ResourceType.QEMU,
                )
            ]
        )

    client.cluster.resources = AsyncMock(side_effect=mock_cluster_resources)

    # Mock the actual GET request payload
    client.request = AsyncMock(
        return_value={
            "vmid": 101,
            "status": "running",
            "qmpstatus": "running",
            "name": "vm",
            "cpus": 1,
            "cpu": 0.5,
            "mem": 1,
            "maxmem": 1,
            "disk": 1,
            "maxdisk": 1,
            "uptime": 100,
            "netin": 0,
            "netout": 0,
        }
    )

    endpoint = QemuStatusEndpoint(client, "pve-01", 101)
    status = await endpoint.current()

    assert isinstance(status, QemuStatus)
    assert status.status == "running"  # Pulled from mocked fallback
    client.cluster.resources.assert_awaited_once()


@pytest.mark.asyncio
async def test_lxc_current_missing_node_exception():
    """Ensure LXC current throws a KeyError if the VMID doesn't exist anywhere."""
    client = MagicMock()
    client.cluster_resources = None
    client.cluster.resources = AsyncMock()  # Fallback finds nothing

    endpoint = LXCStatusEndpoint(client, "pve-01", 999)
    with pytest.raises(ResourceNotFoundError, match="could not be located anywhere"):
        await endpoint.current()


@pytest.mark.asyncio
async def test_access_permissions():
    """Test permission map endpoint."""
    client = MagicMock()
    client.request = AsyncMock(return_value={"/vms/101": {"VM.PowerMgmt": 1}})

    endpoint = AccessEndpoint(client)
    perms = await endpoint.permissions()

    assert perms.has_vm_permission(101, "VM.PowerMgmt") is True


@pytest.mark.asyncio
async def test_tasks_endpoint(mock_cluster_backup_tasks):
    """Test task history retrieval with parameters."""
    client = MagicMock()
    client.request = AsyncMock(return_value=mock_cluster_backup_tasks)

    node = NodeEndpoint(client, "pve-01")

    # Test specific params
    await node.tasks(typefilter="vzdump", limit=50)
    client.request.assert_called_with(
        "GET", "nodes/pve-01/tasks", params={"typefilter": "vzdump", "limit": 50}
    )


@pytest.mark.asyncio
async def test_qemu_snapshot_creation():
    """Test QEMU snapshot creation with full payload options."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value="UPID:pve-01:00001234...")

    endpoint = NodeEndpoint(mock_client, "pve-01").qemu(100)

    upid = await endpoint.status.snapshot(
        snap_name="pre-upgrade",
        snap_description="Backup before OS update",
        snap_state=True,
    )

    assert upid == "UPID:pve-01:00001234..."
    mock_client.request.assert_called_once_with(
        "POST",
        "nodes/pve-01/qemu/100/snapshot",
        data={
            "snapname": "pre-upgrade",
            "vmstate": 1,
            "description": "Backup before OS update",
        },
    )


@pytest.mark.asyncio
async def test_lxc_snapshot_creation():
    """Test LXC snapshot creation without description."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value="UPID:pve-01:00005678...")

    endpoint = NodeEndpoint(mock_client, "pve-01").lxc(202)

    upid = await endpoint.status.snapshot(
        snap_name="quick-snap",
        snap_description=None,
        snap_state=False,
    )

    assert upid == "UPID:pve-01:00005678..."
    mock_client.request.assert_called_once_with(
        "POST",
        "nodes/pve-01/lxc/202/snapshot",
        data={
            "snapname": "quick-snap",
            "vmstate": False,
        },
    )


@pytest.mark.asyncio
async def test_node_endpoint_version():
    """Test node version information."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        return_value={"release": "7.4-3", "repoid": "123456", "version": "7.4"}
    )

    endpoint = NodeEndpoint(mock_client, "pve-01")
    version = await endpoint.version()

    assert version.release == "7.4-3"
    assert version.repoid == "123456"
    assert version.version == "7.4"

    mock_client.request.assert_called_with("GET", "nodes/pve-01/version")


@pytest.mark.asyncio
async def test_node_apt_update():
    """Test node apt available update(s) information."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        return_value=[
            {
                "Description": "Security update",
                "Arch": "amd64",
                "Package": "openssl",
                "Priority": "important",
                "Section": "security",
                "Title": "OpenSSL update",
                "Version": "1.1.1u-1",
                "NotifyStatus": "",
                "OldVersion": "1.1.1t-1",
            }
        ]
    )

    endpoint = NodeEndpoint(mock_client, "pve-01")
    updates = await endpoint.apt().update()

    assert len(updates.items) == 1
    item = updates.items[0]

    assert item.package == "openssl"
    assert item.arch == AptArchType.AMD64
    assert item.version == "1.1.1u-1"

    mock_client.request.assert_called_with("GET", "nodes/pve-01/apt/update")
