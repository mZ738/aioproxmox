"""aioproxmox summaries worked out of what the API already lists: backups, node ports, load."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any, Final

from mashumaro.config import BaseConfig

from .pve import NodeTask, ProxmoxVEDataClass


@dataclass(slots=True)
class LastBackup:
    """A node's most recent finished `vzdump` run, from the archived task log.

    A run still in progress is not in that list, which is deliberate: it
    has no end time and no verdict yet, and reporting it would make every
    backup look like a failure while it runs.
    """

    started: datetime | None
    finished: datetime | None
    status: str | None
    guests: str | None
    user: str | None

    @property
    def duration(self) -> int | None:
        """How long the run took, in seconds."""
        if (
            self.started is None
            or self.finished is None
            or self.finished < self.started
        ):
            return None
        return int((self.finished - self.started).total_seconds())

    @property
    def ok(self) -> bool | None:
        """Whether the run's verdict was OK - `job errors` (a partial run) is not."""
        return None if self.status is None else self.status == "OK"

    @classmethod
    def from_tasks(cls, tasks: list[NodeTask]) -> LastBackup | None:
        """The newest vzdump task, or None for a node that never ran one."""
        runs = [task for task in tasks if task.task_type == "vzdump"]
        if not runs:
            return None
        task = runs[0]
        return cls(
            started=_utc(task.starttime),
            finished=_utc(task.endtime),
            status=task.status,
            # The task's `id` is the guests the run covered - "100" or
            # "100,101" - and is empty for a job that backs up everything.
            guests=task.id or None,
            user=task.user or None,
        )


@dataclass(slots=True)
class RunningBackup:
    """The `vzdump` run in progress on a node, from the active task list."""

    since: datetime | None
    guests: str | None

    @classmethod
    def from_tasks(cls, tasks: list[NodeTask]) -> RunningBackup | None:
        """The vzdump task in progress, or None."""
        runs = [task for task in tasks if task.task_type == "vzdump"]
        if not runs:
            return None
        return cls(since=_utc(runs[0].starttime), guests=runs[0].id or None)


def _utc(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=UTC) if value else None


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
