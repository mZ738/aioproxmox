"""Tests for what Proxmox requires before it answers a route."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from aioproxmox._privilege_data import CHECKS
from aioproxmox.privileges import privileges_for, required_privileges

sys.path.insert(0, str(Path(__file__).parent.parent / "script"))

from generate_privileges import UnsupportedCheck, alternatives, entries


def test_a_single_privilege_on_a_node() -> None:
    """Test the plain case: one privilege, one path, all of them needed."""
    check = required_privileges("GET", "nodes/{node}/apt/update")

    assert check is not None
    assert [(p.path, p.privileges, p.any_of) for p in check] == [
        ("/nodes/{node}", ("Sys.Modify",), False)
    ]
    assert check.render() == "['perm','/nodes/{node}',['Sys.Modify']]"


def test_either_of_two_privileges() -> None:
    """Test the guest agent, where one of two privileges is enough.

    Worth having from the schema rather than from memory: the
    documentation is usually quoted as `VM.GuestAgent.Audit` alone, and
    `VM.GuestAgent.Unrestricted` satisfies it just as well.
    """
    check = required_privileges("POST", "nodes/{node}/qemu/{vmid}/agent/ping")

    assert check is not None
    privileges = next(iter(check))
    assert privileges.path == "/vms/{vmid}"
    assert privileges.privileges == (
        "VM.GuestAgent.Audit",
        "VM.GuestAgent.Unrestricted",
    )
    assert privileges.any_of
    assert check.render() == (
        "['perm','/vms/{vmid}',"
        "['VM.GuestAgent.Audit','VM.GuestAgent.Unrestricted'],'any',1]"
    )


def test_a_privilege_on_either_path() -> None:
    """Test `or`: the disk list takes Sys.Audit on the root or on the node."""
    check = required_privileges("GET", "nodes/{node}/disks/list")

    assert check is not None
    assert [p.path for p in check] == ["/", "/nodes/{node}"]
    assert check.render() == (
        "['perm','/',['Sys.Audit']] or ['perm','/nodes/{node}',['Sys.Audit']]"
    )


def test_the_three_shapes_a_check_comes_in() -> None:
    """Test the reader of the schema's own check syntax, including `or`.

    Proxmox writes a leaf check as `["perm", path, [privileges]]`, appends
    `"any", 1` where one privilege of the list is enough, and combines
    leaves with `["or", check, check]`. Held against the syntax, so it
    does not hang on whichever endpoint happens to use a form.
    """
    # The generator hands back (path, privileges, any_of) per alternative.
    assert alternatives(["perm", "/nodes/{node}", ["Sys.Audit"]]) == (
        ("/nodes/{node}", ("Sys.Audit",), False),
    )
    assert alternatives(
        ["perm", "/vms/{vmid}", ["VM.GuestAgent.Audit", "VM.X"], "any", 1]
    ) == (("/vms/{vmid}", ("VM.GuestAgent.Audit", "VM.X"), True),)
    assert alternatives(
        [
            "or",
            ["perm", "/", ["Sys.Audit"]],
            ["perm", "/nodes/{node}", ["Sys.Audit"]],
        ]
    ) == (
        ("/", ("Sys.Audit",), False),
        ("/nodes/{node}", ("Sys.Audit",), False),
    )


def test_the_node_commands_need_power_management() -> None:
    """Test a command route, which is where a refused button ends up."""
    check = required_privileges("POST", "nodes/{node}/status")

    assert check is not None
    assert check.render() == "['perm','/nodes/{node}',['Sys.PowerMgmt']]"


@pytest.mark.parametrize(
    "path",
    [
        # Proxmox filters these by what the credentials may see instead.
        "cluster/resources",
        "nodes/{node}/qemu",
        "access/permissions",
        # vzdump describes its requirement in prose, not as a check.
        "nodes/{node}/vzdump",
    ],
)
def test_routes_without_a_stated_check(path: str) -> None:
    """Test a route the schema states no check for says so, rather than guessing.

    Held against the schema as well: if Proxmox starts stating a check for
    one of these, this fails rather than passing because the map happens
    not to hold the route.
    """
    schema = json.loads(
        (Path(__file__).parent.parent / "schema" / "pve_api.json").read_text(
            encoding="utf-8"
        )
    )
    for method, info in schema[f"/{path}"].items():
        assert "check" not in info, f"{method} {path} states {info['check']} now"
    assert required_privileges("GET", path) is None
    assert required_privileges("POST", path) is None


def test_a_path_as_it_is_actually_called() -> None:
    """Test the lookup that takes a real path and fills the ids in."""
    check = privileges_for("POST", "nodes/pve/qemu/101/agent/ping")

    assert check is not None
    assert check.render() == (
        "['perm','/vms/101',"
        "['VM.GuestAgent.Audit','VM.GuestAgent.Unrestricted'],'any',1]"
    )
    assert privileges_for("GET", "nodes/pve/apt/update") is not None
    assert (
        privileges_for("GET", "nodes/pve/apt/update").render()  # type: ignore[union-attr]
        == "['perm','/nodes/pve',['Sys.Modify']]"
    )


def test_a_path_nobody_calls() -> None:
    """Test something outside the library's routes is not answered with a guess."""
    assert privileges_for("GET", "nodes/pve/no/such/thing") is None
    assert privileges_for("DELETE", "nodes/pve/apt/update") is None
    # An empty segment is no node.
    assert privileges_for("GET", "nodes//apt/update") is None


def test_a_value_that_contradicts_the_path() -> None:
    """Test a value passed along has to agree with the path it fills."""
    with pytest.raises(ValueError, match="vmid=999"):
        privileges_for("POST", "nodes/pve/qemu/101/agent/ping", vmid=999)
    # Agreeing, or only filling what the path leaves open, is fine.
    check = privileges_for("POST", "nodes/pve/qemu/101/agent/ping", vmid=101)
    assert check is not None
    assert next(iter(check)).path == "/vms/101"
    # A path that is no route's has nothing to contradict.
    assert privileges_for("POST", "nodes/pve/qemu/101/no/such", vmid=999) is None
    # And a value the route has no placeholder for changes nothing.
    assert privileges_for("GET", "nodes/pve/status", storage="local") == (
        privileges_for("GET", "nodes/pve/status")
    )


def test_the_generated_data_is_what_the_schema_says() -> None:
    """Test the committed module is what the generator would write now.

    A refreshed schema that changes a privilege has to be regenerated;
    this is what says so, instead of the library quietly naming the
    privilege of a Proxmox release ago.
    """
    assert entries() == CHECKS


def test_a_check_shape_that_is_not_described_raises() -> None:
    """Test the generator refuses shapes it cannot describe, rather than flattening them."""
    with pytest.raises(UnsupportedCheck):
        alternatives(["and", ["perm", "/", ["Sys.Audit"]]])
    with pytest.raises(UnsupportedCheck):
        alternatives([])
    # The clone routes: a plain requirement without the pool condition
    # would read as less than Proxmox asks for.
    with pytest.raises(UnsupportedCheck):
        alternatives(["perm", "/vms/{vmid}", ["VM.Clone"], "require_param", "pool"])
