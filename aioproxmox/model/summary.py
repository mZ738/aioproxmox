"""aioproxmox summaries worked out of what a node lists: its ports and its load."""

from dataclasses import dataclass, field
import re
from typing import Any, Final

from mashumaro.config import BaseConfig

from .pve import ProxmoxVEDataClass

MAC_ALTNAME: Final = re.compile(r"^enx([0-9a-f]{12})$")


@dataclass(slots=True)
class NodeInterface(ProxmoxVEDataClass):
    """One entry of `/nodes/{node}/network`."""

    iface: str
    type: str | None = None
    active: int | None = None
    address: str | None = None
    cidr: str | None = None
    gateway: str | None = None
    method: str | None = None
    altnames: list[str] = field(default_factory=list)

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True

    @property
    def mac(self) -> str | None:
        """The port's hardware address, decoded from its MAC-based altname.

        Bridges and bonds have no address of their own in the listing; a
        physical port shows up as `type: eth` with its predictable and its
        MAC-based names under `altnames`.
        """
        if self.type != "eth":
            return None
        for altname in self.altnames:
            if match := MAC_ALTNAME.match(altname):
                digits = match.group(1)
                return ":".join(digits[i : i + 2] for i in range(0, 12, 2))
        return None


def node_mac_addresses(interfaces: list[NodeInterface]) -> list[str]:
    """The hardware addresses of a node's physical ports, in listing order."""
    found: list[str] = []
    for interface in interfaces:
        if (mac := interface.mac) and mac not in found:
            found.append(mac)
    return found


def load_average(value: Any) -> tuple[float, float, float] | None:
    """The node's load average, which Proxmox reports as three strings."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        one, five, fifteen = (float(entry) for entry in value)
    except TypeError, ValueError:
        return None
    return (one, five, fifteen)
