"""aioproxmox models for Proxmox Virtualisation Engine."""

from dataclasses import dataclass, field
from enum import StrEnum
import logging
from typing import Annotated, Any, Self

from mashumaro import DataClassDictMixin
from mashumaro.config import BaseConfig
from mashumaro.types import Discriminator

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ProxmoxVEDataClass(DataClassDictMixin):
    """Base aioproxmox class."""

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Self:
        """Map raw data."""
        return cls.from_dict(data)

    @classmethod
    def list_from_api(cls, raw: list[dict[str, Any]]) -> list[Self]:
        """Map raw lists."""
        return [cls.from_dict(item) for item in raw or []]


class ResourceType(StrEnum):
    """Proxmox cluster resource types."""

    NODE = "node"
    QEMU = "qemu"
    LXC = "lxc"
    STORAGE = "storage"
    NETWORK = "network"
    POOL = "pool"
    SDN = "sdn"
    OPENVZ = "openvz"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: Any) -> ResourceType:
        _LOGGER.warning("Unknown Proxmox resource type encountered: %r", value)
        return cls.UNKNOWN


class OperationalStatus(StrEnum):
    """Operational status of resources."""

    ONLINE = "online"
    OFFLINE = "offline"
    RUNNING = "running"
    STOPPED = "stopped"
    AVAILABLE = "available"
    UNKNOWN = "unknown"
    OK = "ok"

    @classmethod
    def _missing_(cls, value: Any) -> OperationalStatus:
        _LOGGER.warning("Unknown Proxmox resource status encountered: %r", value)
        return cls.UNKNOWN


class AptArchType(StrEnum):
    """Architecture types from apt."""

    ARMHF = "armhf"
    ARM64 = "arm64"
    AMD64 = "amd64"
    PPC64EL = "ppc64el"
    RISC64 = "riscv64"
    S390X = "s390x"
    UNKNOWN = "unknown"
    ALL = "all"

    @classmethod
    def _missing_(cls, value: Any) -> AptArchType:
        _LOGGER.warning("Unknown Proxmox apt architecture type encountered: %r", value)
        return cls.UNKNOWN


class QmpStatus(StrEnum):
    """QMP engine status of QEMU."""

    RUNNING = "running"
    STOPPED = "stopped"
    FROZEN = "frozen"
    PAUSED = "paused"
    PRELAUNCH = "prelaunch"
    POSTMIGRATE = "postmigrate"


class StoragePluginType(StrEnum):
    """Proxmox storage backend plugins."""

    DIR = "dir"
    LVMTHIN = "lvmthin"
    LVM = "lvm"
    NFS = "nfs"
    PBS = "pbs"
    CEPHFS = "cephfs"
    RBD = "rbd"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: Any) -> StoragePluginType:
        _LOGGER.warning("Unknown Proxmox storage plugin type encountered: %r", value)
        return cls.UNKNOWN


def deserialize_tags(value: Any) -> list[str]:
    """Cleanly split Proxmox semicolon-separated tag strings for Home Assistant."""
    if isinstance(value, str):
        return [tag.strip() for tag in value.split(";") if tag.strip()]
    if isinstance(value, list):
        return [str(tag).strip() for tag in value if str(tag).strip()]
    return []


@dataclass(slots=True)
class ClusterNodeResource(ProxmoxVEDataClass):
    """Represents a physical Proxmox Node on Cluster Level."""

    id: str
    node: str
    status: OperationalStatus
    resource_type: ResourceType = field(metadata={"alias": "type"})
    # Capacity fields are NOT included when node
    # is offline/inactive
    cpu: float | None = None
    maxcpu: int | None = None
    mem: int | None = None
    maxmem: int | None = None
    disk: int | None = None
    maxdisk: int | None = None
    uptime: int | None = None

    class Config(BaseConfig):
        """Class configuration."""

        allow_unknown_fields = True  # Drops cgroup-mode


@dataclass(slots=True)
class ResourceMemoryStats(DataClassDictMixin):
    """Memory resources."""

    total: int
    used: int
    free: int
    available: int | None = None


@dataclass(slots=True)
class ResourceDiskStats(DataClassDictMixin):
    """Storage resources."""

    total: int
    used: int
    free: int
    avail: int | None = None


@dataclass(slots=True)
class NodeSwapStats(DataClassDictMixin):
    """Swap resources."""

    total: int
    free: int
    used: int


@dataclass(slots=True)
class NodeCpuInfo(DataClassDictMixin):
    """CPU resources."""

    cpus: int
    cores: int
    sockets: int
    model: str
    vendor: str
    mhz: str | None = None


@dataclass(slots=True)
class NodeStatus(DataClassDictMixin):
    """Represents the complete nested structure returned by /nodes/{node}/status."""

    uptime: int
    cpu: float
    idle: float
    memory: ResourceMemoryStats
    swap: NodeSwapStats
    rootfs: ResourceDiskStats
    cpuinfo: NodeCpuInfo
    pveversion: str
    loadavg: list[str] = field(default_factory=list)
    wait: float | None = None

    class Config(BaseConfig):
        """Class configuration."""

        allow_unknown_fields = (
            True  # Drops boot-info, kversion, and kernel metadata gracefully
        )


@dataclass(slots=True)
class NodeVersion(DataClassDictMixin):
    """Represents data returned by /nodes/{node}/version."""

    release: str
    repoid: str
    version: str


@dataclass(slots=True)
class NodeAptUpdateProperty(DataClassDictMixin):
    """Represents individual apt package update property status."""

    description: str = field(metadata={"alias": "Description"})
    arch: AptArchType = field(metadata={"alias": "Arch"})
    package: str = field(metadata={"alias": "Package"})
    priority: str = field(metadata={"alias": "Priority"})
    section: str = field(metadata={"alias": "Section"})
    title: str = field(metadata={"alias": "Title"})
    version: str = field(metadata={"alias": "Version"})
    origin: str = field(default="", metadata={"alias": "Origin"})

    notify_status: str = field(default="", metadata={"alias": "NotifyStatus"})
    old_version: str = field(default="", metadata={"alias": "OldVersion"})


@dataclass(slots=True)
class NodeAptUpdate(DataClassDictMixin):
    """Represents the all apt update status' returned by /nodes/{node}/apt/update."""

    items: list[NodeAptUpdateProperty]


@dataclass(slots=True)
class ClusterQemuResource(ProxmoxVEDataClass):
    """Represents a QEMU Virtual Machine on Cluster Level."""

    id: str
    vmid: int
    name: str
    node: str
    status: OperationalStatus
    template: int
    maxcpu: int
    maxmem: int
    maxdisk: int
    resource_type: ResourceType = field(metadata={"alias": "type"})
    cpu: float = 0.0
    mem: int = 0
    memhost: int = 0
    disk: int = 0
    diskread: int = 0
    diskwrite: int = 0
    netin: int = 0
    netout: int = 0
    uptime: int = 0
    tags: list[str] = field(
        default_factory=list, metadata={"deserialize": deserialize_tags}
    )


@dataclass(slots=True)
class QemuResource(DataClassDictMixin):
    """Represents a QEMU Virtual Machine summary on a specific Node."""

    vmid: int
    name: str
    status: OperationalStatus
    cpus: int
    cpu: float = 0.0
    mem: int = 0
    maxmem: int = 0
    disk: int = 0
    maxdisk: int = 0
    memhost: int = 0
    netin: int = 0
    netout: int = 0
    uptime: int = 0
    pid: int | None = None
    template: int = 0


@dataclass(slots=True)
class QemuStatus(DataClassDictMixin):
    """Represents the real-time operational telemetry of a specific QEMU virtual machine."""

    vmid: int
    status: OperationalStatus
    qmpstatus: QmpStatus
    name: str
    cpus: int
    cpu: float
    mem: int
    maxmem: int
    disk: int
    maxdisk: int
    uptime: int
    netin: int
    netout: int
    agent: int | None = None

    class Config(BaseConfig):
        """Class configuration."""

        allow_unknown_fields = (
            True  # Drops the internal "ha" block and "clipboard" safely
        )


@dataclass(slots=True)
class ClusterContainerResource(ProxmoxVEDataClass):
    """Represents an LXC Container on Cluster Level."""

    id: str
    vmid: int
    name: str
    node: str
    status: OperationalStatus
    template: int
    maxcpu: int
    maxmem: int
    maxdisk: int
    resource_type: ResourceType = field(metadata={"alias": "type"})
    cpu: float = 0.0
    mem: int = 0
    memhost: int = 0
    disk: int = 0
    diskread: int = 0
    diskwrite: int = 0
    netin: int = 0
    netout: int = 0
    uptime: int = 0
    tags: list[str] = field(
        default_factory=list, metadata={"deserialize": deserialize_tags}
    )


@dataclass(slots=True)
class ContainerResource(DataClassDictMixin):
    """Represents a LXC Container summary on a specific Node."""

    vmid: int
    name: str
    status: OperationalStatus
    cpus: int
    cpu: float = 0.0
    mem: int = 0
    maxmem: int = 0
    disk: int = 0
    maxdisk: int = 0
    netin: int = 0
    netout: int = 0
    swap: int = 0
    maxswap: int = 0
    uptime: int = 0
    pid: int | None = None
    template: int = 0


@dataclass(slots=True)
class LXCStatus(DataClassDictMixin):
    """Represents the real-time operational telemetry of a specific LXC container."""

    vmid: int
    status: str
    name: str
    cpus: int
    cpu: float
    mem: int
    maxmem: int
    swap: int
    maxswap: int
    disk: int
    maxdisk: int
    diskread: int
    diskwrite: int
    netin: int
    netout: int
    uptime: int
    pid: int | None = None

    class Config(BaseConfig):
        """Class configuration."""

        allow_unknown_fields = True  # Safely ignores the PSI 'pressure' strings and 'ha' flags, also removes flags which are already in cluster resources


@dataclass(slots=True)
class ClusterStorageResource(ProxmoxVEDataClass):
    """Represents a defined Cluster Storage pool."""

    id: str
    storage: str
    node: str
    status: OperationalStatus
    plugintype: StoragePluginType
    shared: int
    content: str
    resource_type: ResourceType = field(metadata={"alias": "type"})
    disk: int = 0
    maxdisk: int = 0


@dataclass(slots=True)
class NodeStorageResource(ProxmoxVEDataClass):
    """Represents a defined Node Storage pool."""

    enabled: bool
    shared: bool
    active: bool
    storage: str
    resource_type: StoragePluginType = field(metadata={"alias": "type"})
    # Capacity fields are NOT included when the node or storage
    # is offline/inactive
    total: int | None = None
    avail: int | None = None
    used: int | None = None
    used_fraction: float | None = None


@dataclass(slots=True)
class NodeStorageResources(ProxmoxVEDataClass):
    """List of storage on node."""

    storages: list[NodeStorageResource]


@dataclass(slots=True)
class NodeTask(ProxmoxVEDataClass):
    """Single task status."""

    id: str
    node: str
    pid: int
    pstart: int
    starttime: int
    task_type: str = field(metadata={"alias": "type"})
    upid: str
    user: str

    endtime: int | None = None
    status: str | None = None


@dataclass(slots=True)
class NodeTasks(ProxmoxVEDataClass):
    """List of task nodes."""

    tasks: list[NodeTask]


@dataclass(slots=True)
class ClusterNetworkResource(ProxmoxVEDataClass):
    """Represents a SDN or physical cluster network interface definition."""

    id: str
    node: str
    resource_type: ResourceType = field(metadata={"alias": "type"})
    status: str = "unknown"

    class Config(BaseConfig):
        """Class configuration."""

        allow_unknown_fields = True


def route_pve_resource(data: Any) -> type:
    """Map the raw API type attribute directly to the correct parsing target."""
    if isinstance(data, dict) and (kind := data.get("type")):
        match kind:
            case ResourceType.NODE | "node":
                return ClusterNodeResource
            case ResourceType.QEMU | "qemu":
                return ClusterQemuResource
            case ResourceType.LXC | "lxc":
                return ClusterContainerResource
            case ResourceType.STORAGE | "storage":
                return ClusterStorageResource
            case ResourceType.NETWORK | "network":
                return ClusterNetworkResource
    raise ValueError(f"Unsupported or missing Proxmox resource type tag: {data}")


ProxmoxResource = Annotated[
    ClusterNodeResource
    | ClusterQemuResource
    | ClusterContainerResource
    | ClusterStorageResource
    | ClusterNetworkResource,
    Discriminator(
        variant_tagger_fn=route_pve_resource,
        include_subtypes=True,  # Satisfies the internal Mashumaro safety guardrail
    ),
]


def deserialize_resource_list(value: Any) -> list[Any]:
    """Manually route each raw dictionary item to its corresponding dataclass."""
    if not isinstance(value, list):
        return []

    parsed_items: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        kind = item.get("type")
        try:
            match kind:
                case "node":
                    parsed_items.append(ClusterNodeResource.from_dict(item))
                case "qemu":
                    parsed_items.append(ClusterQemuResource.from_dict(item))
                case "lxc":
                    parsed_items.append(ClusterContainerResource.from_dict(item))
                case "storage":
                    parsed_items.append(ClusterStorageResource.from_dict(item))
                case "network":
                    parsed_items.append(ClusterNetworkResource.from_dict(item))
                case "pool" | "sdn" | "openvz":
                    continue
                case _:
                    _LOGGER.warning("Skipping unknown Proxmox resource type: %r", kind)
        except Exception:
            _LOGGER.exception("Failed to parse resource item %r", item.get("id"))

    return parsed_items


@dataclass(slots=True)
class ClusterResourcesCollection(ProxmoxVEDataClass):
    """Container mapping to deserialize raw /cluster/resources payloads securely."""

    resources: list[
        ClusterNodeResource
        | ClusterQemuResource
        | ClusterContainerResource
        | ClusterStorageResource
        | ClusterNetworkResource
    ] = field(
        default_factory=list,
        metadata={"deserialize": deserialize_resource_list},
    )

    def __iter__(self) -> Any:
        """Provide iteration."""
        return iter(self.resources)

    @property
    def nodes(self) -> list[ClusterNodeResource]:
        """Return the node resources."""
        return [r for r in self.resources if isinstance(r, ClusterNodeResource)]

    @property
    def qemu(self) -> list[ClusterQemuResource]:
        """Return the QEMU virtual machine resources."""
        return [r for r in self.resources if isinstance(r, ClusterQemuResource)]

    @property
    def lxc(self) -> list[ClusterContainerResource]:
        """Return the LXC container resources."""
        return [r for r in self.resources if isinstance(r, ClusterContainerResource)]

    @property
    def storage(self) -> list[ClusterStorageResource]:
        """Return the storage resources."""
        return [r for r in self.resources if isinstance(r, ClusterStorageResource)]


@dataclass(slots=True)
class ClusterCache:
    """Centralized, indexed state hub storing real-time telemetry metrics."""

    nodes: dict[str, ClusterNodeResource] = field(default_factory=dict)
    qemu: dict[int, ClusterQemuResource] = field(default_factory=dict)
    lxc: dict[int, ClusterContainerResource] = field(default_factory=dict)
    storage: dict[str, ClusterStorageResource] = field(default_factory=dict)
