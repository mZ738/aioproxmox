"""Helpers for Proxmox."""

import logging

from .model.pve import (
    ClusterCache,
    ClusterContainerResource,
    ClusterNodeResource,
    ClusterQemuResource,
    ClusterResourcesCollection,
    ClusterStorageResource,
)

_LOGGER = logging.getLogger(__name__)


def pve_find_node_in_cache(
    cluster_resources: ClusterResourcesCollection,
    vmid: int,
) -> str | None:
    """Helper to extract the node location from the resource model instances."""
    if not cluster_resources:
        return None
    guests: list[ClusterQemuResource | ClusterContainerResource] = [
        *cluster_resources.qemu,
        *cluster_resources.lxc,
    ]
    for res in guests:
        if res.vmid == vmid:
            return str(res.node)
    return None


def pve_cluster_cache(
    cluster_resources: ClusterResourcesCollection,
) -> ClusterCache:
    """Purge stale telemetry models from cache when vanished from cluster resources."""
    if not cluster_resources:
        return ClusterCache()

    nodes, qemu, lxc, storage = {}, {}, {}, {}

    for res in cluster_resources.resources:
        match res:
            case ClusterNodeResource():
                nodes[res.node] = res
            case ClusterQemuResource():
                qemu[res.vmid] = res
            case ClusterContainerResource():
                lxc[res.vmid] = res
            case ClusterStorageResource():
                storage[f"{res.node}:{res.storage}"] = res

    return ClusterCache(nodes=nodes, qemu=qemu, lxc=lxc, storage=storage)
