"""aioproxmox models for what a guest reports about itself: agent data, snapshots."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
import ipaddress
from typing import Any

from mashumaro.config import BaseConfig

from .pve import ProxmoxVEDataClass


@dataclass(slots=True)
class GuestFilesystem(ProxmoxVEDataClass):
    """One filesystem as the QEMU guest agent's `get-fsinfo` lists it."""

    name: str
    mountpoint: str | None = None
    type: str | None = None
    used_bytes: int | None = field(default=None, metadata={"alias": "used-bytes"})
    total_bytes: int | None = field(default=None, metadata={"alias": "total-bytes"})
    disk: list[dict[str, Any]] = field(default_factory=list)

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True

    @property
    def device(self) -> str:
        """The device behind the filesystem, or its name when the agent gives none."""
        if self.disk and isinstance(self.disk[0], dict) and self.disk[0].get("dev"):
            return str(self.disk[0]["dev"])
        return self.name


def guest_disk_usage(
    filesystems: list[GuestFilesystem],
) -> tuple[int | None, int | None]:
    """Sum a guest's disk usage the way its own tools would.

    A device mounted more than once (bind mounts, a Windows volume seen as
    C: and a GUID path) counts once, at its largest reported figure. The
    total is only given when every filesystem that contributed to the used
    figure also reported one, so the two numbers describe the same set.
    """
    used_by_device: dict[str, int] = {}
    total_by_device: dict[str, int] = {}
    for filesystem in filesystems:
        if not filesystem.disk or filesystem.used_bytes is None:
            continue
        device = filesystem.device
        used_by_device[device] = max(
            used_by_device.get(device, 0), filesystem.used_bytes
        )
        if filesystem.total_bytes is not None:
            total_by_device[device] = max(
                total_by_device.get(device, 0), filesystem.total_bytes
            )
    if not used_by_device:
        return None, None
    used = sum(used_by_device.values())
    total = (
        sum(total_by_device.values())
        if total_by_device.keys() == used_by_device.keys()
        else None
    )
    return used, total


@dataclass(slots=True)
class GuestAddress(ProxmoxVEDataClass):
    """One address of a guest interface, as the agent lists it."""

    ip_address: str = field(metadata={"alias": "ip-address"})
    ip_address_type: str | None = field(
        default=None, metadata={"alias": "ip-address-type"}
    )
    prefix: int | None = None

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True


@dataclass(slots=True)
class GuestInterface(ProxmoxVEDataClass):
    """One network interface of a guest.

    A VM's agent (`agent/network-get-interfaces`) lists `ip-addresses`; a
    container (`lxc/{vmid}/interfaces`) lists `inet`/`inet6` strings with a
    prefix length. Both end up here.
    """

    name: str
    hardware_address: str | None = field(
        default=None, metadata={"alias": "hardware-address"}
    )
    hwaddr: str | None = None
    ip_addresses: list[GuestAddress] = field(
        default_factory=list, metadata={"alias": "ip-addresses"}
    )
    inet: str | None = None
    inet6: str | None = None

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True

    @property
    def mac(self) -> str | None:
        """The MAC address, whichever field it came in."""
        return self.hardware_address or self.hwaddr

    @property
    def addresses(self) -> list[str]:
        """Every address of the interface, without prefix lengths."""
        found = [item.ip_address for item in self.ip_addresses]
        found.extend(raw.split("/", 1)[0] for raw in (self.inet, self.inet6) if raw)
        return found


def _shown(address: str) -> bool:
    """Tell an address worth showing from loopback and link-local noise."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified)


def guest_addresses(interfaces: list[GuestInterface]) -> dict[str, list[str]]:
    """Return the shown addresses by interface, in interface order; loopback left out."""
    result: dict[str, list[str]] = {}
    for interface in interfaces:
        shown = [address for address in interface.addresses if _shown(address)]
        if shown:
            result[interface.name] = shown
    return result


def primary_address(interfaces: list[GuestInterface]) -> str | None:
    """The address to show for a guest: its first IPv4, else its first IPv6."""
    addresses = [
        address for shown in guest_addresses(interfaces).values() for address in shown
    ]
    if not addresses:
        return None
    return next(
        (a for a in addresses if ipaddress.ip_address(a).version == 4), addresses[0]
    )


@dataclass(slots=True)
class Snapshot(ProxmoxVEDataClass):
    """One entry of a guest's snapshot list.

    The list always ends with a `current` pseudo entry standing for the
    live state; `is_current` tells it apart, it is not a snapshot.
    """

    name: str
    snaptime: int | None = None
    description: str | None = None
    parent: str | None = None
    vmstate: int | None = None
    running: int | None = None

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True

    @property
    def is_current(self) -> bool:
        """Whether this is the live state rather than a snapshot."""
        return self.name == "current"

    @property
    def taken_at(self) -> datetime | None:
        """When the snapshot was taken, aware, UTC."""
        return datetime.fromtimestamp(self.snaptime, tz=UTC) if self.snaptime else None


def snapshots_taken(entries: list[Snapshot]) -> list[Snapshot]:
    """The real snapshots, newest first."""
    taken = [entry for entry in entries if not entry.is_current]
    taken.sort(key=lambda entry: entry.snaptime or 0, reverse=True)
    return taken
