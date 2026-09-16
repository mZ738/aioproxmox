"""Testing."""

import json
from pathlib import Path
from typing import Any

import pytest

USERDATA = Path(__file__).parent.parent / "userdata"


def mock_pve_fixture(
    file: str = "single_924_cluster_resources.json",
) -> Any:
    """Represents raw data payload from a single-node Proxmox cluster."""
    userdata_file = USERDATA / file
    raw = json.loads(userdata_file.read_text(encoding="utf-8"))
    return raw.get("data", {})


@pytest.fixture
def mock_cluster_resources() -> list[dict]:
    """Return fixture cluster resources."""
    return mock_pve_fixture()


@pytest.fixture
def mock_cluster_backup_tasks() -> list[dict]:
    """Return fixture cluster backup tasks."""
    return mock_pve_fixture("single_924_cluster_tasks_vzdump.json")


@pytest.fixture
def mock_cluster_node_status() -> dict:
    """Return fixture cluster node status."""
    return mock_pve_fixture("single_924_cluster_node_status.json")


@pytest.fixture
def mock_cluster_node_storage() -> list[dict]:
    """Return fixture cluster node storage."""
    return mock_pve_fixture("single_924_cluster_node_storage.json")


@pytest.fixture
def mock_cluster_node_storage_offline() -> list[dict]:
    """Return fixture cluster node storage partially offline."""
    return mock_pve_fixture("single_924_cluster_node_storage_offline.json")


@pytest.fixture
def mock_pve_dual_node_cluster_raw() -> list[dict]:
    """Represents raw data payload from a 2-node cluster with mixed VMs and LXCs."""
    return [
        # --- Physical Nodes ---
        {
            "id": "node/pve-01",
            "node": "pve-01",
            "type": "node",
            "status": "online",
            "cpu": 0.1,
            "maxcpu": 8,
            "mem": 16000,
            "maxmem": 32000,
            "disk": 1000,
            "maxdisk": 2000,
            "uptime": 100,
        },
        {
            "id": "node/pve-02",
            "node": "pve-02",
            "type": "node",
            "status": "online",
            "cpu": 0.1,
            "maxcpu": 8,
            "mem": 16000,
            "maxmem": 32000,
            "disk": 1000,
            "maxdisk": 2000,
            "uptime": 100,
        },
        # --- Virtual Machines (QEMU) ---
        {
            "id": "qemu/101",
            "vmid": 101,
            "name": "prod-app",
            "node": "pve-01",
            "type": "qemu",
            "status": "running",
            "template": 0,
            "maxcpu": 2,
            "maxmem": 4000,
            "maxdisk": 40,
        },
        {
            "id": "qemu/102",
            "vmid": 102,
            "name": "dev-db",
            "node": "pve-02",
            "type": "qemu",
            "status": "running",
            "template": 0,
            "maxcpu": 2,
            "maxmem": 4000,
            "maxdisk": 40,
        },
        # --- Linux Containers (LXC) ---
        {
            "id": "lxc/201",
            "vmid": 201,
            "name": "nginx-proxy",
            "node": "pve-01",
            "type": "lxc",
            "status": "running",
            "template": 0,
            "maxcpu": 1,
            "maxmem": 1000,
            "maxdisk": 10,
        },
        {
            "id": "lxc/202",
            "vmid": 202,
            "name": "mqtt-broker",
            "node": "pve-02",
            "type": "lxc",
            "status": "running",
            "template": 0,
            "maxcpu": 1,
            "maxmem": 1000,
            "maxdisk": 10,
        },
    ]


@pytest.fixture
def mock_node_disks() -> list[dict]:
    """Return fixture physical disks of a node."""
    return mock_pve_fixture("single_924_node_disks_list.json")


@pytest.fixture
def mock_node_disk_smart() -> dict:
    """Return fixture SMART data of an ATA disk."""
    return mock_pve_fixture("single_924_node_disk_smart_ata.json")


@pytest.fixture
def mock_node_zfs() -> list[dict]:
    """Return fixture ZFS pools of a node."""
    return mock_pve_fixture("single_924_node_disks_zfs.json")


@pytest.fixture
def mock_agent_fsinfo() -> dict:
    """Return fixture guest agent filesystem info, with its `result` envelope."""
    return mock_pve_fixture("single_924_qemu_agent_fsinfo.json")


@pytest.fixture
def mock_agent_interfaces() -> dict:
    """Return fixture guest agent network interfaces, with its `result` envelope."""
    return mock_pve_fixture("single_924_qemu_agent_network_interfaces.json")


@pytest.fixture
def mock_lxc_interfaces() -> list[dict]:
    """Return fixture container interfaces."""
    return mock_pve_fixture("single_924_lxc_interfaces.json")


@pytest.fixture
def mock_snapshots() -> list[dict]:
    """Return fixture snapshot list, `current` last."""
    return mock_pve_fixture("single_924_qemu_snapshot.json")
