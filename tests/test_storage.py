"""Tests for storage as the cluster lists it: shared once, local per node."""

from aioproxmox.model.pve import ClusterResourcesCollection
from aioproxmox.model.storage import (
    backup_storages,
    shared_storage,
    shared_storage_names,
)


def _storage_rows() -> ClusterResourcesCollection:
    def row(
        node: str, name: str, shared: int, status: str, content: str = "images"
    ) -> dict:
        return {
            "id": f"storage/{node}/{name}",
            "storage": name,
            "node": node,
            "type": "storage",
            "status": status,
            "plugintype": "nfs" if shared else "dir",
            "shared": shared,
            "content": content,
            "disk": 1000,
            "maxdisk": 4000,
        }

    return ClusterResourcesCollection.from_dict(
        {
            "resources": [
                row("pve1", "nas", 1, "available", "backup,images"),
                row("pve2", "nas", 1, "unknown", "backup,images"),
                row("pve3", "nas", 1, "available", "backup,images"),
                row("pve1", "local", 0, "available", "vztmpl,backup,iso"),
                row("pve2", "local", 0, "available", "vztmpl,backup,iso"),
                row("pve1", "local-lvm", 0, "available", "rootdir,images"),
            ]
        }
    )


def test_shared_storage_is_collapsed_to_one_row():
    """The row of a node that sees it, and every node that does."""
    resources = _storage_rows()
    assert shared_storage_names(resources) == {"nas"}

    nas = shared_storage(resources, "nas")
    assert nas is not None
    assert nas.row.node == "pve1"
    assert nas.nodes == ("pve1", "pve3")
    assert shared_storage(resources, "local") is None
    assert shared_storage(resources, "nope") is None
    assert backup_storages(resources) == ["local", "nas"]


def test_a_shared_storage_nobody_sees_still_has_a_row():
    """Every node reporting it unavailable: the first row, with no nodes."""
    resources = ClusterResourcesCollection.from_dict(
        {
            "resources": [
                {
                    "id": "storage/pve1/nas",
                    "storage": "nas",
                    "node": "pve1",
                    "type": "storage",
                    "status": "unknown",
                    "plugintype": "nfs",
                    "shared": 1,
                    "content": "backup",
                }
            ]
        }
    )
    nas = shared_storage(resources, "nas")
    assert nas is not None
    assert not nas.nodes
