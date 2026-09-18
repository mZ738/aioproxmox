"""What Proxmox requires before it answers a route.

Every endpoint in the API schema says which privilege it checks, on which
path, and whether one of several is enough:

    GET /nodes/{node}/qemu/{vmid}/agent/get-fsinfo
        ["perm", "/vms/{vmid}", ["VM.GuestAgent.Audit",
                                 "VM.GuestAgent.Unrestricted"], "any", 1]

That is worth having in the library rather than in each consumer: a
caller can say what is missing before it makes the call, and say it with
the privilege Proxmox actually wants instead of one read off the
documentation - or worse, parsed out of the error text afterwards.

The data comes from `schema/pve_api.json` and is generated into
`_privilege_data.py`; the shapes are described there.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ._privilege_data import CHECKS


@dataclass(frozen=True, slots=True)
class Privileges:
    """Privileges on one path, as one way of satisfying a route."""

    path: str
    """The ACL path they are needed on, `/vms/{vmid}` or a filled-in `/vms/101`."""
    privileges: tuple[str, ...]
    """The privileges Proxmox names."""
    any_of: bool = False
    """Whether one of them is enough, rather than all."""

    def render(self) -> str:
        """Return the check the way the documentation writes it.

        `['perm','/vms/101',['VM.PowerMgmt']]` - which is what a message
        to a user can carry verbatim, since it is what they will find in
        the permissions section of the Proxmox documentation.
        """
        privileges = ",".join(f"'{privilege}'" for privilege in self.privileges)
        tail = ",'any',1" if self.any_of else ""
        return f"['perm','{self.path}',[{privileges}]{tail}]"


@dataclass(frozen=True, slots=True)
class Check:
    """What a route requires: any one of its alternatives satisfies it."""

    alternatives: tuple[Privileges, ...]

    def __iter__(self) -> Iterator[Privileges]:
        """Iterate the alternatives, so a caller can loop without reaching in."""
        return iter(self.alternatives)

    def render(self) -> str:
        """Return every alternative, separated by `or` where there is a choice."""
        return " or ".join(privileges.render() for privileges in self.alternatives)

    def substitute(self, **values: str | int) -> Check:
        """Return the same check with `{node}` and `{vmid}` filled in."""
        filled = []
        for privileges in self.alternatives:
            path = privileges.path
            for name, value in values.items():
                path = path.replace(f"{{{name}}}", str(value))
            filled.append(
                Privileges(path, privileges.privileges, any_of=privileges.any_of)
            )
        return Check(tuple(filled))


def _check(entry: tuple[tuple[str, tuple[str, ...], bool], ...]) -> Check:
    """Turn one generated entry into a check."""
    return Check(
        tuple(
            Privileges(path, privileges, any_of=any_of)
            for path, privileges, any_of in entry
        )
    )


def required_privileges(method: str, path: str) -> Check | None:
    """Return what a route requires, by its template: `nodes/{node}/apt/update`.

    None means the schema states no check for it. That is not the same as
    "anyone may call it": several endpoints filter what they return by
    what the credentials may see (`cluster/resources`, `nodes/{node}/qemu`),
    and a few describe their requirement in prose instead (`vzdump` wants
    `VM.Backup` on the guests and `Datastore.AllocateSpace` on the
    storage). Both are in the documentation, neither is machine-readable.
    """
    entry = CHECKS.get((method.upper(), path.strip("/")))
    return _check(entry) if entry else None


def privileges_for(method: str, path: str, **values: str | int) -> Check | None:
    """Return what a route requires, by the path as it is actually called.

    `privileges_for("GET", "nodes/pve/apt/update")` finds the template
    the path belongs to and hands back the check with the node filled in,
    so the answer names `/nodes/pve` rather than `/nodes/{node}`.
    """
    method = method.upper()
    parts = path.strip("/").split("/")
    for (known_method, template), entry in CHECKS.items():
        if known_method != method:
            continue
        segments = template.split("/")
        if len(segments) != len(parts):
            continue
        filled = dict(values)
        for segment, part in zip(segments, parts, strict=True):
            if segment.startswith("{") and segment.endswith("}"):
                filled[segment[1:-1]] = part
                continue
            if segment != part:
                break
        else:
            return _check(entry).substitute(**filled)
    return None
