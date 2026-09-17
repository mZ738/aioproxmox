"""aioproxmox models for a node's and a cluster's health: certificates, subscription, replication, Ceph, HA."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import logging
from typing import Any, Final

from mashumaro.config import BaseConfig

from .pve import ProxmoxVEDataClass

_LOGGER = logging.getLogger(__name__)


def _utc(value: Any) -> datetime | None:
    """Turn epoch seconds into an aware UTC datetime, or None for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except OverflowError, OSError, ValueError:
        _LOGGER.warning("Unusable timestamp %r", value)
        return None


# --- certificates ---

# The certificate that actually serves the API: a custom or ACME one when
# it exists, else the one the cluster's own CA issued. The cluster CA
# itself (pve-root-ca.pem) is valid for ten years and not worth watching.
CERTIFICATE_PREFERENCE: Final[tuple[str, ...]] = ("pveproxy-ssl.pem", "pve-ssl.pem")


@dataclass(slots=True)
class Certificate(ProxmoxVEDataClass):
    """One entry of `/nodes/{node}/certificates/info`."""

    filename: str
    subject: str | None = None
    issuer: str | None = None
    notafter: int | None = None
    notbefore: int | None = None
    fingerprint: str | None = None
    san: list[str] = field(default_factory=list)

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True

    @property
    def expires(self) -> datetime | None:
        """When the certificate stops being valid."""
        return _utc(self.notafter)


def serving_certificate(certificates: list[Certificate]) -> Certificate | None:
    """Pick the certificate that serves the API out of the node's list."""
    by_filename = {certificate.filename: certificate for certificate in certificates}
    for filename in CERTIFICATE_PREFERENCE:
        if filename in by_filename:
            return by_filename[filename]
    return None


# --- subscription ---


class SubscriptionStatus(StrEnum):
    """The states Proxmox documents for a node's subscription (PVE::API2::Subscription)."""

    NEW = "new"
    NOTFOUND = "notfound"
    ACTIVE = "active"
    INVALID = "invalid"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: Any) -> SubscriptionStatus:
        _LOGGER.warning("Unknown Proxmox subscription status encountered: %r", value)
        return cls.UNKNOWN


@dataclass(slots=True)
class Subscription(ProxmoxVEDataClass):
    """What `/nodes/{node}/subscription` reports.

    `key`, `serverid` and `signature` are deliberately not modelled: they
    identify the machine and the subscription, and nothing needs them.
    """

    status: SubscriptionStatus = SubscriptionStatus.UNKNOWN
    level: str | None = None
    productname: str | None = None
    nextduedate: str | None = None
    message: str | None = None

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True


# --- replication ---


@dataclass(slots=True)
class ReplicationJob(ProxmoxVEDataClass):
    """One job of `/nodes/{node}/replication`."""

    id: str
    guest: int | None = None
    vmtype: str | None = None
    target: str | None = None
    source: str | None = None
    schedule: str | None = None
    disable: int | None = None
    fail_count: int | None = None
    error: str | None = None
    last_sync: int | None = None
    last_try: int | None = None
    next_sync: int | None = None
    duration: float | None = None

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True

    @property
    def disabled(self) -> bool:
        """Whether somebody switched the job off on purpose."""
        return bool(self.disable)

    @property
    def failing(self) -> bool:
        """Whether the job's last runs failed - disabled jobs never count."""
        return not self.disabled and bool(self.fail_count)

    @property
    def last_synced(self) -> datetime | None:
        """When the job last replicated successfully."""
        return _utc(self.last_sync)


@dataclass(slots=True)
class ReplicationHealth:
    """Replication health of a node, from its jobs.

    The oldest successful sync across the active jobs is the useful figure:
    it says how far behind the furthest-behind target is, where the newest
    would hide a job that stopped replicating days ago.
    """

    jobs: int
    failing_jobs: list[ReplicationJob]
    oldest_sync: datetime | None

    @property
    def failing(self) -> bool:
        """Whether any active job is failing."""
        return bool(self.failing_jobs)

    @classmethod
    def from_jobs(cls, jobs: list[ReplicationJob]) -> ReplicationHealth:
        """Work the health out of the job list."""
        active = [job for job in jobs if not job.disabled]
        syncs = [job.last_synced for job in active if job.last_synced is not None]
        return cls(
            jobs=len(jobs),
            failing_jobs=[job for job in active if job.failing],
            oldest_sync=min(syncs) if syncs else None,
        )


# --- Ceph ---


class CephHealth(StrEnum):
    """Ceph's own health states, as `ceph -s` reports them."""

    OK = "HEALTH_OK"
    WARN = "HEALTH_WARN"
    ERR = "HEALTH_ERR"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: Any) -> CephHealth:
        _LOGGER.warning("Unknown Ceph health status encountered: %r", value)
        return cls.UNKNOWN


@dataclass(slots=True)
class CephCheck:
    """One failing check of the Ceph health block."""

    check: str
    severity: str | None = None
    message: str | None = None


@dataclass(slots=True)
class CephStatus:
    """What `/cluster/ceph/status` says, reduced to what a dashboard wants.

    The health block is read in full. Of the placement group map only the
    two totals are taken - bytes used and bytes available across the OSDs,
    what `ceph -s` prints as the cluster's usage. The rest of that map, and
    the OSD and monitor maps, are a different question.
    """

    health: CephHealth
    checks: list[CephCheck]
    bytes_used: int | None
    bytes_total: int | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> CephStatus:
        """Read the parts wanted out of the raw status."""
        health_block = raw.get("health")
        health_block = health_block if isinstance(health_block, dict) else {}
        status = health_block.get("status")
        health = CephHealth(status) if status is not None else CephHealth.UNKNOWN

        checks: list[CephCheck] = []
        raw_checks = health_block.get("checks")
        if isinstance(raw_checks, dict):
            for name, check in raw_checks.items():
                if not isinstance(check, dict):
                    continue
                summary = check.get("summary")
                message = summary.get("message") if isinstance(summary, dict) else None
                checks.append(
                    CephCheck(
                        check=str(name),
                        severity=(
                            check["severity"]
                            if isinstance(check.get("severity"), str)
                            else None
                        ),
                        message=message if isinstance(message, str) else None,
                    )
                )

        pgmap = raw.get("pgmap")
        pgmap = pgmap if isinstance(pgmap, dict) else {}
        return cls(
            health=health,
            checks=checks,
            bytes_used=_positive(pgmap.get("bytes_used")),
            bytes_total=_positive(pgmap.get("bytes_total")),
        )


def _positive(value: Any) -> int | None:
    """A positive integer, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


# --- HA ---

HA_ARMED_STATES: Final[frozenset[str]] = frozenset(
    {"armed", "standby", "disarming", "disarmed"}
)
HA_RESOURCE_MODES: Final[frozenset[str]] = frozenset({"freeze", "ignore"})

# CRM service states that mean an incident is in progress: the guest is
# fenced, being recovered after a fence, or stuck in error. Read from
# `crm_state` (the raw CRM state) rather than `state`, which is a verbose
# display value the API rewrites to "ignore" while HA is disarmed.
HA_SERVICE_ERROR_STATES: Final[frozenset[str]] = frozenset(
    {"error", "fence", "recovery"}
)

# How old the CRM master timestamp may get before the master counts as dead.
# PVE::API2::HA::Status uses the same 30 s, but only inside its localized
# display string, so the check is repeated here on the structured field.
HA_CRM_MASTER_DEAD_AFTER: Final[timedelta] = timedelta(seconds=30)


@dataclass(slots=True)
class HAServiceProblem:
    """An HA resource in error, fence or recovery."""

    sid: str
    node: str
    crm_state: str


@dataclass(slots=True)
class HAStatus:
    """The cluster's HA status from `/cluster/ha/status/current`.

    Only the structured fields of each entry are read; the `status` field
    is a localized display string and must not be parsed.
    """

    quorate: bool | None = None
    crm_master: str | None = None
    crm_master_last_seen: datetime | None = None
    armed_state: str | None = None
    resource_mode: str | None = None
    resources_total: int = 0
    resources_error: list[HAServiceProblem] = field(default_factory=list)

    def crm_master_stale(self, now: datetime | None = None) -> bool | None:
        """Whether the CRM master has not refreshed its status for 30 s."""
        if self.crm_master_last_seen is None:
            return None
        now = now or datetime.now(tz=UTC)
        return now - self.crm_master_last_seen > HA_CRM_MASTER_DEAD_AFTER

    @classmethod
    def from_api(cls, entries: list[Any]) -> HAStatus:
        """Read the entries by type; anything that is not a dict is skipped."""
        status = cls()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            match entry.get("type"):
                case "quorum":
                    # PVE serializes the boolean as 1/0 depending on version.
                    if (value := entry.get("quorate")) is not None:
                        status.quorate = value in (True, 1, "1")
                case "master":
                    if (node := entry.get("node")) is not None:
                        status.crm_master = str(node)
                    status.crm_master_last_seen = _utc(entry.get("timestamp"))
                case "fencing":
                    status.armed_state = _allowed(entry, "armed-state", HA_ARMED_STATES)
                    status.resource_mode = _allowed(
                        entry, "resource_mode", HA_RESOURCE_MODES
                    )
                case "service":
                    status.resources_total += 1
                    if (crm_state := entry.get("crm_state")) in HA_SERVICE_ERROR_STATES:
                        status.resources_error.append(
                            HAServiceProblem(
                                sid=str(entry.get("sid", "")),
                                node=str(entry.get("node", "")),
                                crm_state=str(crm_state),
                            )
                        )
        return status


def _allowed(entry: dict[str, Any], key: str, allowed: frozenset[str]) -> str | None:
    """An enum field of an HA status entry, or None if unusable."""
    value = entry.get(key)
    if value in allowed:
        return str(value)
    if value is not None:
        _LOGGER.warning("Unknown value %r for Proxmox HA status field %s", value, key)
    return None


@dataclass(slots=True)
class GuestWithoutBackup(ProxmoxVEDataClass):
    """One entry of `/cluster/backup-info/not-backed-up`."""

    vmid: int
    type: str | None = None
    name: str | None = None

    class Config(BaseConfig):
        """DataClass configuration."""

        allow_unknown_fields = True
