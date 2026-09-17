"""aioproxmox helpers for storage as the cluster lists it: shared once, local per node."""

from dataclasses import dataclass

from .pve import ClusterResourcesCollection, ClusterStorageResource, OperationalStatus


def storage_rows(resources: ClusterResourcesCollection) -> list[ClusterStorageResource]:
    """The storage rows of the resource list."""
    return [r for r in resources if isinstance(r, ClusterStorageResource)]


def shared_storage_names(resources: ClusterResourcesCollection) -> set[str]:
    """The names of the storages the cluster marks as shared.

    Proxmox lists a storage once per node that has it configured, so an NFS
    export or a Ceph pool a four-node cluster mounts everywhere appears four
    times with the same figures. These are the ones to show once.
    """
    return {row.storage for row in storage_rows(resources) if row.shared}


@dataclass(slots=True)
class SharedStorage:
    """One shared storage: the row to read its figures from, and who sees it."""

    name: str
    row: ClusterStorageResource
    nodes: tuple[str, ...]


def shared_storage(
    resources: ClusterResourcesCollection, name: str
) -> SharedStorage | None:
    """Collapse a shared storage's per-node rows into one.

    The figures come from a node that currently reports the storage
    available, so a node that lost its mount does not zero the numbers for
    everyone; `nodes` lists every node that does see it. With no node
    seeing it, the first row still names it - with `nodes` empty.
    """
    candidates = sorted(
        (row for row in storage_rows(resources) if row.storage == name and row.shared),
        key=lambda row: row.node,
    )
    if not candidates:
        return None
    available = [row for row in candidates if row.status == OperationalStatus.AVAILABLE]
    return SharedStorage(
        name=name,
        row=available[0] if available else candidates[0],
        nodes=tuple(row.node for row in available),
    )


def backup_storages(resources: ClusterResourcesCollection) -> list[str]:
    """The names of the storages a backup can be written to, sorted.

    A storage takes backups when `backup` is among its content types; the
    listing carries it once per node, so the names are collapsed.
    """
    return sorted(
        {
            row.storage
            for row in storage_rows(resources)
            if "backup" in row.content.split(",")
        }
    )
