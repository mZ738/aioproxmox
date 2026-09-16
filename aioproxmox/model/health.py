"""aioproxmox models for a node's health: certificates, subscription, replication."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
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
