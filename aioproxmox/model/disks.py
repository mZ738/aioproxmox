"""aioproxmox models for a node's physical disks, their SMART data and ZFS pools."""

from dataclasses import dataclass, field
from enum import StrEnum
import logging
import re
from typing import Any, Final

from mashumaro.config import BaseConfig

from .pve import ProxmoxVEDataClass

_LOGGER = logging.getLogger(__name__)


class DiskType(StrEnum):
    """What kind of drive `/nodes/{node}/disks/list` says a disk is."""

    HDD = "hdd"
    SSD = "ssd"
    NVME = "nvme"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: Any) -> DiskType:
        _LOGGER.warning("Unknown Proxmox disk type encountered: %r", value)
        return cls.UNKNOWN


def _wearout(value: Any) -> int | None:
    """Wearout arrives as a number or the string `N/A` for drives without one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


@dataclass(slots=True)
class NodeDisk(ProxmoxVEDataClass):
    """One physical disk as `/nodes/{node}/disks/list` reports it."""

    devpath: str
    size: int
    type: DiskType
    model: str | None = None
    serial: str | None = None
    vendor: str | None = None
    wwn: str | None = None
    by_id_link: str | None = None
    health: str | None = None
    used: str | None = None
    rpm: int | None = None
    gpt: int | None = None
    osdid: int | None = None
    wearout: int | None = field(default=None, metadata={"deserialize": _wearout})

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True


# The SMART attribute ids the summary reads, as smartctl numbers them for
# ATA drives. NVMe and SAS drives report text instead; their labels are
# mapped onto the same ids below so one reader serves all three.
SMART_POWER_HOURS: Final = 9
SMART_POWER_CYCLES: Final = 12
SMART_POWER_LOSS: Final = 174
SMART_TEMPERATURE_AIR: Final = 190
SMART_TEMPERATURE: Final = 194
SMART_LIFE_LEFT: Final = 231

SMART_TEXT_LABELS: Final[dict[str, int]] = {
    # NVMe
    "Temperature": SMART_TEMPERATURE,
    "Power Cycles": SMART_POWER_CYCLES,
    "Power On Hours": SMART_POWER_HOURS,
    # SAS
    "Current Drive Temperature": SMART_TEMPERATURE,
    "Accumulated start-stop cycles": SMART_POWER_CYCLES,
    "Accumulated power on time, hours:minutes": SMART_POWER_HOURS,
}


def smart_text_attributes(text: str) -> list[dict[str, Any]]:
    """Turn smartctl's text output into the attribute shape the ATA path uses.

    Each line is `label: value`. The label is matched whole rather than by
    prefix, so `Temperature Sensor 1` does not pass for `Temperature`; the
    one SAS label that carries a colon of its own is looked for first.
    Lines with a label the summary does not read are left out.
    """
    attributes: list[dict[str, Any]] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        label, value = None, ""
        for known in SMART_TEXT_LABELS:
            if ":" in known and line.startswith(known):
                label, value = known, line[len(known) :]
                break
        if label is None:
            head, sep, tail = line.partition(":")
            if not sep:
                continue
            label, value = head.strip(), tail
        if label not in SMART_TEXT_LABELS:
            continue
        attributes.append(
            {
                "name": label,
                "raw": value.strip().replace(",", ""),
                "id": SMART_TEXT_LABELS[label],
            }
        )
    return attributes


def leading_int(value: Any) -> int | None:
    """Return the integer a SMART value starts with, or None.

    SMART values arrive as text and carry whatever the drive felt like
    reporting: `36`, `36 (Min/Max 20/45)`, `3728h+12m`, or a bare `-` where
    a virtual NVMe reports no temperature at all. The integer in front is
    the reading; anything without one is not one.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    match = re.match(r"\s*(\d+)", value.replace(",", ""))
    return int(match.group(1)) if match else None


@dataclass(slots=True)
class SmartSummary:
    """The handful of SMART readings a dashboard wants, whatever the drive's shape."""

    temperature: int | None = None
    temperature_air: int | None = None
    power_hours: int | None = None
    power_cycles: int | None = None
    life_left: int | None = None
    power_loss: int | None = None

    @classmethod
    def from_attributes(cls, attributes: list[Any]) -> SmartSummary:
        """Pick the readings out of a list of SMART attributes.

        A value that is not a number is skipped rather than raised on: a
        temperature reported as `-` is no reading, not a broken disk.
        """
        wanted = {
            SMART_POWER_CYCLES: ("power_cycles", "raw"),
            SMART_TEMPERATURE: ("temperature", "raw"),
            SMART_TEMPERATURE_AIR: ("temperature_air", "raw"),
            SMART_POWER_HOURS: ("power_hours", "raw"),
            SMART_LIFE_LEFT: ("life_left", "value"),
            SMART_POWER_LOSS: ("power_loss", "raw"),
        }
        summary = cls()
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            number = leading_int(attribute.get("id"))
            if number is None or number not in wanted:
                continue
            key, source = wanted[number]
            if (reading := leading_int(attribute.get(source))) is not None:
                setattr(summary, key, reading)
        return summary


@dataclass(slots=True)
class DiskSmart(ProxmoxVEDataClass):
    """What `/nodes/{node}/disks/smart?disk=` reports, plus a normalised summary.

    Proxmox hands the data out in two shapes: smartctl's numbered
    `attributes` for ATA drives, and its `text` log for NVMe and SAS drives.
    `summary` reads both.
    """

    health: str | None = None
    type: str | None = None
    attributes: list[dict[str, Any]] = field(default_factory=list)
    text: str | None = None

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True

    @property
    def summary(self) -> SmartSummary:
        """Return the readings, from the attributes or from the text."""
        attributes = self.attributes or (
            smart_text_attributes(self.text) if self.text else []
        )
        return SmartSummary.from_attributes(attributes)


@dataclass(slots=True)
class ZfsPool(ProxmoxVEDataClass):
    """One pool as `/nodes/{node}/disks/zfs` lists it."""

    name: str
    health: str
    size: int
    alloc: int
    free: int
    frag: int | None = None
    dedup: float | None = None

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True

    @property
    def used_fraction(self) -> float | None:
        """Return alloc over size, or None for an empty pool."""
        return self.alloc / self.size if self.size > 0 else None
