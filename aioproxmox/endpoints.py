"""Helper endpoints for compatibility."""

import logging
from typing import Any, cast

from .exceptions import ProxmoxAPIError, ProxmoxError, ResourceNotFoundError
from .helpers import pve_cluster_cache, pve_find_node_in_cache
from .model import PVEPermissions
from .model.disks import DiskSmart, NodeDisk, ZfsPool
from .model.guest import GuestFilesystem, GuestInterface
from .model.pve import (
    ClusterResourcesCollection,
    ContainerResource,
    LXCStatus,
    NodeAptUpdate,
    NodeAptUpdateProperty,
    NodeStatus,
    NodeStorageResource,
    NodeStorageResources,
    NodeTask,
    NodeTasks,
    NodeVersion,
    QemuResource,
    QemuStatus,
)

_LOGGER = logging.getLogger(__name__)


class PostAction:
    """A generic executor that fires a POST request to its configured path when called."""

    def __init__(self, client: Any, path: str) -> None:
        """Initialize POST action."""
        self.client = client
        self.path = path

    def __call__(self, **kwargs: Any) -> Any:
        """Execute action."""
        return self.client.request("POST", self.path, json_data=kwargs)


class NodeActionProperty:
    """Descriptor to dynamically bind a node action endpoint to a PostAction route."""

    def __init__(self, endpoint: str) -> None:
        """Initialize property."""
        self.endpoint = endpoint

    def __get__(self, instance: Any, owner: Any = None) -> PostAction:
        """Call action."""
        if instance is None:
            return self  # type: ignore[return-value]
        return PostAction(instance.client, f"nodes/{instance.node}/{self.endpoint}")


class QemuActionProperty:
    """Descriptor to dynamically bind a QEMU action endpoint to a PostAction route."""

    def __init__(self, endpoint: str) -> None:
        """Initialize property."""
        self.endpoint = endpoint

    def __get__(self, instance: Any, owner: Any = None) -> PostAction:
        """Call action."""
        if instance is None:
            return self  # type: ignore[return-value]
        return PostAction(
            instance.client,
            f"nodes/{instance.node}/qemu/{instance.vmid}/status/{self.endpoint}",
        )


class LXCActionProperty:
    """Descriptor to dynamically bind an LXC action endpoint to a PostAction route."""

    def __init__(self, endpoint: str) -> None:
        """Initialize property."""
        self.endpoint = endpoint

    def __get__(self, instance: Any, owner: Any = None) -> PostAction:
        """Call action."""
        if instance is None:
            return self  # type: ignore[return-value]
        return PostAction(
            instance.client,
            f"nodes/{instance.node}/lxc/{instance.vmid}/status/{self.endpoint}",
        )


def node_action(endpoint: str) -> NodeActionProperty:
    """Factory helper to declare a Node PostAction endpoint."""
    return NodeActionProperty(endpoint)


def qemu_action(endpoint: str) -> QemuActionProperty:
    """Factory helper to declare a QEMU PostAction endpoint."""
    return QemuActionProperty(endpoint)


def lxc_action(endpoint: str) -> LXCActionProperty:
    """Factory helper to declare an LXC PostAction endpoint."""
    return LXCActionProperty(endpoint)


def _agent_result(raw: Any) -> list[dict[str, Any]]:
    """Unwrap the `result` envelope the guest agent commands answer with."""
    entries = raw.get("result") if isinstance(raw, dict) else raw
    return entries if isinstance(entries, list) else []


class QemuAgentEndpoint:
    """Agent endpoint."""

    def __init__(
        self,
        client: Any,
        node: str,
        vmid: int,
    ) -> None:
        """Endpoint initialisation."""
        self.client = client
        self.node = node
        self.vmid = vmid

    async def status(self) -> None:
        """Return generic agent info."""
        raise NotImplementedError

    async def ping(self) -> bool:
        """Ping agent for reply on alive."""
        try:
            await self.client.request(
                "POST", f"nodes/{self.node}/qemu/{self.vmid}/agent/ping"
            )
        except ProxmoxAPIError:
            return False
        return True

    async def fsinfo(self) -> list[GuestFilesystem]:
        """Fetch the guest's filesystems with their usage, from inside the guest.

        The host only knows the size of the virtual disks; this is where a
        VM's disk usage comes from. Needs `VM.GuestAgent.Audit` (Proxmox VE 9)
        and a running agent - otherwise the API answers 500.
        """
        raw = await self.client.request(
            "GET", f"nodes/{self.node}/qemu/{self.vmid}/agent/get-fsinfo"
        )
        return GuestFilesystem.list_from_api(_agent_result(raw))

    async def network_interfaces(self) -> list[GuestInterface]:
        """Fetch the guest's interfaces with their addresses, from inside the guest."""
        raw = await self.client.request(
            "GET", f"nodes/{self.node}/qemu/{self.vmid}/agent/network-get-interfaces"
        )
        return GuestInterface.list_from_api(_agent_result(raw))


class QemuStatusEndpoint:
    """Qemu nested status endpoint."""

    def __init__(
        self,
        client: Any,
        node: str,
        vmid: int,
    ) -> None:
        """Endpoint initialisation."""
        self.client = client
        self.node = node
        self.vmid = vmid

    async def status(self) -> None:
        """Return generic Qemu info."""
        raise NotImplementedError

    async def current(self) -> QemuStatus:
        """Fetch deep sensoric metrics for a QEMU VM, dynamically inferring its host node."""
        node = pve_find_node_in_cache(self.client.cluster_resources, self.vmid)

        if not node:
            _LOGGER.debug(
                "VMID %d not found in internal cache. Executing single fallback cluster fetch.",
                self.vmid,
            )
            try:
                await self.client.cluster.resources()
                node = pve_find_node_in_cache(self.client.cluster_resources, self.vmid)
            except Exception as err:
                raise ResourceNotFoundError(
                    f"Failed to fetch resource map while tracking VMID {self.vmid}"
                ) from err

            if not node:
                raise ResourceNotFoundError(
                    f"Target QEMU VMID {self.vmid} could not be located anywhere in the cluster."
                )

        raw = await self.client.request(
            "GET", f"nodes/{node}/qemu/{self.vmid}/status/current"
        )
        if not isinstance(raw, dict):
            raise ProxmoxError(
                f"Expected dict response from qemu VM status, got {type(raw)}"
            )
        return QemuStatus.from_dict(raw)

    async def snapshot(
        self,
        snap_name: str,
        snap_description: str | None = None,
        snap_state: bool = True,
    ) -> str:
        """Create a new Snapshot for a VM."""
        payload = {
            "snapname": snap_name,
            "vmstate": int(snap_state),  # Note, convert bool back to int
        }
        if snap_description:
            payload["description"] = snap_description

        return str(
            await self.client.request(
                "POST",
                f"nodes/{self.node}/qemu/{self.vmid}/snapshot",
                data=payload,
            )
        )

    start = qemu_action("start")
    stop = qemu_action("stop")
    restart = qemu_action("restart")
    suspend = qemu_action("suspend")
    resume = qemu_action("resume")
    reset = qemu_action("reset")
    shutdown = qemu_action("shutdown")


class QemuEndpoint:
    """Endpoint for Qemu (VM)."""

    def __init__(self, client: Any, node: str, vmid: int) -> None:
        """Endpoint initialisation."""
        self.client = client
        self.node = node
        self.vmid = vmid
        self.status = QemuStatusEndpoint(client, node, vmid)
        self.agent = QemuAgentEndpoint(client, node, vmid)


class LXCStatusEndpoint:
    """LXC nested status endpoint."""

    def __init__(
        self,
        client: Any,
        node: str,
        vmid: int,
    ) -> None:
        """Endpoint initialisation."""
        self.client = client
        self.node = node
        self.vmid = vmid

    async def status(self) -> None:
        """Return generic LXC info."""
        raise NotImplementedError

    async def current(self) -> LXCStatus:
        """Fetch deep sensoric metrics for a container, dynamically inferring its host node."""
        node = pve_find_node_in_cache(self.client.cluster_resources, self.vmid)

        if not node:
            _LOGGER.debug(
                "VMID %d not found in internal cache. Executing single fallback cluster fetch.",
                self.vmid,
            )
            try:
                await self.client.cluster.resources()
                node = pve_find_node_in_cache(self.client.cluster_resources, self.vmid)
            except Exception as err:
                raise ResourceNotFoundError(
                    f"Failed to fetch resource map while tracking VMID {self.vmid}"
                ) from err

            if not node:
                raise ResourceNotFoundError(
                    f"Target container VMID {self.vmid} could not be located anywhere in the cluster."
                )

        raw = await self.client.request(
            "GET", f"nodes/{node}/lxc/{self.vmid}/status/current"
        )
        if not isinstance(raw, dict):
            raise ProxmoxError(
                f"Expected dict response from LCX status, got {type(raw)}"
            )
        return LXCStatus.from_dict(raw)

    async def snapshot(
        self,
        snap_name: str | None = None,
        snap_description: str | None = None,
        snap_state: bool = True,
    ) -> str:
        """Create a new Snapshot for a VM."""
        payload = {
            "snapname": snap_name,
            "vmstate": int(snap_state),  # Note, convert bool back to int
        }
        if snap_description:
            payload["description"] = snap_description

        return str(
            await self.client.request(
                "POST",
                f"nodes/{self.node}/lxc/{self.vmid}/snapshot",
                data=payload,
            )
        )

    start = lxc_action("start")
    restart = lxc_action("restart")
    stop = lxc_action("stop")


class LXCEndpoint:
    """LXC Container endpoint."""

    def __init__(self, client: Any, node: str, vmid: int) -> None:
        """Endpoint initialisation."""
        self.client = client
        self.node = node
        self.vmid = vmid
        self.status = LXCStatusEndpoint(client, node, vmid)

    async def interfaces(self) -> list[GuestInterface]:
        """Fetch the container's interfaces with their addresses; needs it running."""
        raw = await self.client.request(
            "GET", f"nodes/{self.node}/lxc/{self.vmid}/interfaces"
        )
        return GuestInterface.list_from_api(raw if isinstance(raw, list) else [])


class AccessEndpoint:
    """Access endpoint."""

    def __init__(self, client: Any) -> None:
        """Endpoint initialisation."""
        self.client = client

    async def permissions(self) -> PVEPermissions:
        """Fetch the full, granular ACL permissions map for the active session."""
        raw = await self.client.request("GET", "access/permissions")
        self.client.permissions = PVEPermissions.from_api_response(raw)

        return cast(PVEPermissions, self.client.permissions)


class NodeAptEndpoint:
    """APT endpoint for a node."""

    def __init__(self, client: Any, node: str) -> None:
        """APT endpoint for a node."""
        self.client = client
        self.node = node

    async def update(self) -> NodeAptUpdate:
        """Fetch apt update list."""
        raw = await self.client.request("GET", f"nodes/{self.node}/apt/update")
        items = [NodeAptUpdateProperty.from_dict(item) for item in raw or []]
        return NodeAptUpdate(items=items)


class NodeDisksEndpoint:
    """Physical disks, their SMART data and ZFS pools of a node."""

    def __init__(self, client: Any, node: str) -> None:
        """Endpoint initialisation."""
        self.client = client
        self.node = node

    async def all(self) -> list[NodeDisk]:
        """Fetch the node's physical disks.

        Proxmox runs smartctl for the health and wearout columns, which wakes
        a sleeping disk - worth knowing before polling this every minute.
        """
        raw = await self.client.request("GET", f"nodes/{self.node}/disks/list")
        return NodeDisk.list_from_api(raw)

    async def smart(self, devpath: str) -> DiskSmart:
        """Fetch one disk's SMART data; `DiskSmart.summary` reads either shape."""
        raw = await self.client.request(
            "GET", f"nodes/{self.node}/disks/smart", params={"disk": devpath}
        )
        if not isinstance(raw, dict):
            raise ProxmoxError(
                f"Expected dict response from disks/smart, got {type(raw)}"
            )
        return DiskSmart.from_dict(raw)

    async def zfs(self) -> list[ZfsPool]:
        """Fetch the node's ZFS pools."""
        raw = await self.client.request("GET", f"nodes/{self.node}/disks/zfs")
        return ZfsPool.list_from_api(raw)


class NodeEndpoint:
    """Node endpoint."""

    def __init__(self, client: Any, node: str) -> None:
        """Endpoint initialisation."""
        self.client = client
        self.node = node

    def apt(self) -> NodeAptEndpoint:
        """Map APT endpoint."""
        return NodeAptEndpoint(self.client, self.node)

    def disks(self) -> NodeDisksEndpoint:
        """Map the disks endpoint."""
        return NodeDisksEndpoint(self.client, self.node)

    def qemu(self, vmid: int) -> QemuEndpoint:
        """Map individual Qemu endpoint."""
        return QemuEndpoint(self.client, self.node, vmid)

    def lxc(self, vmid: int) -> LXCEndpoint:
        """Map LXC endpoint."""
        return LXCEndpoint(self.client, self.node, vmid)

    async def qemu_all(self) -> list[QemuResource]:
        """Fetch all Qemu resources."""
        raw = await self.client.request("GET", f"nodes/{self.node}/qemu")
        return [QemuResource.from_dict(item) for item in raw]

    async def lxc_all(self) -> list[ContainerResource]:
        """Fetch all LXC resources."""
        raw = await self.client.request("GET", f"nodes/{self.node}/lxc")
        return [ContainerResource.from_dict(item) for item in raw]

    async def status(self) -> NodeStatus:
        """Fetch deep operational status for this physical node."""
        raw = await self.client.request("GET", f"nodes/{self.node}/status")
        return NodeStatus.from_dict(raw)

    async def tasks(
        self, typefilter: str | None = None, limit: int | None = None
    ) -> NodeTasks:
        """Fetch operational history blocks matching specific filters (e.g., vzdump)."""
        params: dict[str, Any] = {}
        if typefilter:
            params["typefilter"] = typefilter
        if limit is not None:
            params["limit"] = limit

        # The Proxmox API handles query constraints cleanly via standard payload mappings
        raw = await self.client.request(
            "GET",
            f"nodes/{self.node}/tasks",
            params=params or None,
        )
        return NodeTasks(tasks=NodeTask.list_from_api(raw))

    async def storage(self) -> NodeStorageResources:
        """Fetch high-level allocations and health for all storages on this node."""
        raw = await self.client.request("GET", f"nodes/{self.node}/storage")
        return NodeStorageResources(storages=NodeStorageResource.list_from_api(raw))

    async def version(self) -> NodeVersion:
        """Fetch PVE version for this physical node."""
        raw = await self.client.request("GET", f"nodes/{self.node}/version")
        return NodeVersion.from_dict(raw)

    reboot = node_action("reboot")
    shutdown = node_action("shutdown")
    suspendall = node_action("suspendall")
    stopall = node_action("stopall")
    startall = node_action("startall")


class ClusterEndpoint:
    """Cluster endpoint."""

    def __init__(self, client: Any) -> None:
        """Endpoint initialisation."""
        self.client = client

    async def resources(self) -> ClusterResourcesCollection:
        """A direct, optimized call returning the complete cluster resources block."""
        raw = await self.client.request("GET", "cluster/resources")
        self.client.cluster_resources = ClusterResourcesCollection.from_dict(
            {"resources": raw}
        )

        # Update cache for rogue entries
        self.client.cluster_cache = pve_cluster_cache(self.client.cluster_resources)

        return cast(ClusterResourcesCollection, self.client.cluster_resources)
