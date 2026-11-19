"""Testing."""

import logging

import pytest

from aioproxmox.model.pve import (
    ClusterNodeResource,
    ClusterQemuResource,
    ClusterResourcesCollection,
    ClusterStorageResource,
    OperationalStatus,
    ResourceType,
    StoragePluginType,
    deserialize_resource_list,
    deserialize_tags,
    route_pve_resource,
)


def test_deserialize_pve_cluster_resources(mock_cluster_resources):
    """Ensure raw array parses cleanly into distinct structured object types."""
    payload = {"resources": mock_cluster_resources}
    collection = ClusterResourcesCollection.from_dict(payload)

    assert len(collection.resources) == 12

    # 1. Verify Physical Node Parsing & Camel-Case Hyphen Aliasing
    node = collection.resources[6]
    assert isinstance(node, ClusterNodeResource)
    assert node.id == "node/pve-01"
    assert node.resource_type == ResourceType.NODE
    assert node.maxcpu == 16

    # 2. Verify VM Parsing & Delimited Semicolon String-to-List Conversion
    vm = collection.resources[0]
    assert isinstance(vm, ClusterQemuResource)
    assert vm.name == "vm102"
    assert vm.resource_type == ResourceType.QEMU

    # 3. Verify Storage Sub-Type Targeting
    storage = collection.resources[7]
    assert isinstance(storage, ClusterStorageResource)
    assert storage.storage == "local"
    assert storage.status == OperationalStatus.AVAILABLE


def test_deserialize_resilience_to_unknown_elements(caplog):
    """Unknown types/enums shouldn't crash collection loading; they fallback safely."""
    malformed_payload = {
        "resources": [
            {
                "id": "qemu/999",
                "vmid": 999,
                "name": "future-vm",
                "node": "pve-01",
                "type": "qemu",
                "status": "some-new-status-from-pve-update",  # Evaluates via _missing_ fallback
                "template": 0,
                "maxcpu": 2,
                "maxmem": 1024,
                "maxdisk": 1024,
            },
            {
                "id": "unknown/123",
                "type": "brand-new-pve-resource-type",  # Handled cleanly inside loop
            },
        ]
    }

    collection = ClusterResourcesCollection.from_dict(malformed_payload)

    # The unknown collection type string drops out cleanly without crashing the loop
    assert len(collection.resources) == 1

    # The unknown status fallback on the matching VM becomes OperationalStatus.UNKNOWN
    parsed_vm = collection.resources[0]
    assert parsed_vm.status == OperationalStatus.UNKNOWN
    assert "Unknown Proxmox resource status encountered" in caplog.text
    assert (
        "Skipping unknown Proxmox resource type: 'brand-new-pve-resource-type'"
        in caplog.text
    )


def test_deserialize_handles_broken_items(caplog):
    """A completely broken dictionary item shouldn't prevent parsing valid elements."""
    malformed_payload = {
        "resources": [
            {
                "id": "node/broken-node",
                "type": "node",
                # Missing all required fields like node, status, cpu, maxcpu, etc.
            },
            {
                "id": "storage/working-storage",
                "storage": "local-lvm",
                "node": "pve-01",
                "type": "storage",
                "status": "available",
                "plugintype": "lvmthin",
                "shared": 0,
                "content": "rootdir",
            },
        ]
    }

    with caplog.at_level(logging.ERROR):
        collection = ClusterResourcesCollection.from_dict(malformed_payload)

    # The broken item dropped into the except block, leaving only the healthy storage item
    assert len(collection.resources) == 1
    assert isinstance(collection.resources[0], ClusterStorageResource)
    assert "Failed to parse resource item 'node/broken-node'" in caplog.text


def test_deserialize_tags():
    """Test string and list handling for tags."""
    # Semicolon separated
    assert deserialize_tags("homeautomation; production; important") == [
        "homeautomation",
        "production",
        "important",
    ]
    # List of strings
    assert deserialize_tags([" tag1 ", "tag2"]) == ["tag1", "tag2"]
    # Invalid types fallback safely
    assert deserialize_tags(None) == []
    assert deserialize_tags(123) == []


def test_route_pve_resource_exceptions():
    """Ensure route_pve_resource raises ValueError for unsupported types."""
    with pytest.raises(
        ValueError, match="Unsupported or missing Proxmox resource type tag"
    ):
        route_pve_resource({"type": "invalid_type"})

    with pytest.raises(
        ValueError, match="Unsupported or missing Proxmox resource type tag"
    ):
        route_pve_resource({})


def test_resource_enum_fallbacks(caplog):
    """Ensure ResourceType and StoragePluginType handle unknown strings gracefully."""
    with caplog.at_level(logging.WARNING):
        res_type = ResourceType("_future_pve_type_")
        assert res_type == ResourceType.UNKNOWN
        assert "Unknown Proxmox resource type encountered" in caplog.text
        caplog.clear()

        for kind in ("pool", "sdn", "openvz"):
            assert ResourceType(kind) != ResourceType.UNKNOWN
        assert not caplog.records

        plugin_type = StoragePluginType("_future_plugin_")
        assert plugin_type == StoragePluginType.UNKNOWN
        assert "Unknown Proxmox storage plugin type encountered" in caplog.text


def test_deserialize_lxc_and_network(caplog):
    """Ensure LXC and Network nodes parse correctly from raw array."""
    payload = {
        "resources": [
            {
                "id": "lxc/202",
                "vmid": 202,
                "name": "docker-host",
                "node": "pve-01",
                "type": "lxc",
                "status": "running",
                "template": 0,
                "maxcpu": 2,
                "maxmem": 4096,
                "maxdisk": 10000,
            },
            {
                "id": "network/sdn1",
                "node": "pve-01",
                "type": "network",
                "status": "active",
            },
        ]
    }

    collection = ClusterResourcesCollection.from_dict(payload)

    assert len(collection.resources) == 2

    lxc = collection.resources[0]
    assert lxc.resource_type == ResourceType.LXC
    assert lxc.vmid == 202

    network = collection.resources[1]
    assert network.resource_type == ResourceType.NETWORK
    assert network.id == "network/sdn1"

    # Test Collection Iteration (covers __iter__)
    iterable = list(collection)
    assert len(iterable) == 2
    assert iterable[0].vmid == 202


def test_deserialize_resource_list_raw(caplog):
    """Directly test the static deserializer list parsing and edge cases."""
    raw_list = [
        {
            "type": "node",
            "id": "node/1",
            "node": "1",
            "status": "online",
            "cpu": 1,
            "maxcpu": 1,
            "mem": 1,
            "maxmem": 1,
            "disk": 1,
            "maxdisk": 1,
            "uptime": 1,
        },
        {"type": "pool", "id": "/pool/example"},  # Documented, skipped silently
        {"type": "sdn", "id": "sdn/pve-01/localnetwork"},
        {"type": "openvz", "id": "openvz/100"},
        {"type": "unknown_type_entirely"},  # Handled by case _
        "not_a_dict_should_skip",  # Handled by isinstance(item, dict) check
    ]

    with caplog.at_level(logging.WARNING):
        parsed = deserialize_resource_list(raw_list)
    assert len(parsed) == 1
    assert parsed[0].resource_type == ResourceType.NODE
    # Only the undocumented type warns
    assert [r.getMessage() for r in caplog.records] == [
        "Skipping unknown Proxmox resource type: 'unknown_type_entirely'"
    ]

    # Check invalid master payload entirely
    assert not deserialize_resource_list(None)
    assert not deserialize_resource_list({"not": "a list"})


def test_cluster_collection_iteration(mock_pve_dual_node_cluster_raw):
    """Ensure the iteration dunder method behaves correctly."""
    collection = ClusterResourcesCollection.from_dict(
        {"resources": mock_pve_dual_node_cluster_raw}
    )

    # Passing the collection directly to list() calls __iter__ under the hood
    items = list(collection)
    assert len(items) == 6


def test_offline_node_without_capacity_fields():
    """Ensure an offline node without capacity fields still parses."""
    collection = ClusterResourcesCollection.from_dict(
        {
            "resources": [
                {
                    "id": "node/pve-02",
                    "node": "pve-02",
                    "status": "offline",
                    "type": "node",
                }
            ]
        }
    )

    node = collection.resources[0]
    assert isinstance(node, ClusterNodeResource)
    assert node.status == OperationalStatus.OFFLINE
    assert node.cpu is None
    assert node.maxmem is None
    assert node.uptime is None


def test_cluster_collection_default_empty():
    """Ensure the collection defaults to no resources."""
    collection = ClusterResourcesCollection()

    assert not collection.resources
    assert not list(collection)
    assert not ClusterResourcesCollection.from_dict({}).resources
