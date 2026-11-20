"""Check every route in `endpoints.py` against Proxmox's own API schema.

Paths were read off the API viewer by hand, and hand-reading goes wrong:
`nodes/{node}/reboot` looked obvious and does not exist (a node is
rebooted through `nodes/{node}/status` with a `command`), and guests were
sent a `restart` that Proxmox does not know. Both were only found by
trying them against a real cluster.

Proxmox publishes the whole API as a schema, so this does not have to be
found by trying: the endpoints are read out of the source and compared
with `schema/pve_api.json` - path, method, the parameter names that are
visible in the call, and the values the call fixes where the schema lists
the ones it accepts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from script.routes import INDIRECT, Route, collect

SCHEMA = Path(__file__).parent.parent / "schema" / "pve_api.json"


def _schema() -> dict[str, dict[str, Any]]:
    """Return the trimmed Proxmox API schema, routes only.

    The file also carries a `_source` entry saying where the extract came
    from; everything that is not a path is skipped here.
    """
    parsed: dict[str, Any] = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return {path: info for path, info in parsed.items() if path.startswith("/")}


ROUTES, INDIRECT_CALLS = collect()
API = _schema()


def test_the_endpoints_are_read_out_of_the_source() -> None:
    """Test the reading itself still works, so a refactor cannot mute this file.

    A count alone would drift with every endpoint added, so two routes
    that are not going away are named as well.
    """
    assert len(ROUTES) > 20
    called = {(route.method, route.path) for route in ROUTES}
    assert ("GET", "nodes/{node}/status") in called
    assert ("POST", "nodes/{node}/qemu/{vmid}/status/start") in called
    assert INDIRECT_CALLS == INDIRECT


@pytest.mark.parametrize("route", ROUTES, ids=repr)
def test_the_route_exists_in_proxmox(route: Route) -> None:
    """Test Proxmox answers this path, and answers it with this method."""
    path = f"/{route.path}"
    assert path in API, f"{route}: no such path in the Proxmox API"
    assert route.method in API[path], (
        f"{route}: Proxmox answers {sorted(API[path])} here, not {route.method}"
    )


@pytest.mark.parametrize(
    "route", [route for route in ROUTES if route.parameters], ids=repr
)
def test_the_parameters_are_the_ones_proxmox_takes(route: Route) -> None:
    """Test the names sent along are parameters of that route."""
    known = set(API[f"/{route.path}"][route.method]["parameters"])
    assert route.parameters <= known, (
        f"{route}: {sorted(route.parameters - known)} is not a parameter here; "
        f"Proxmox takes {sorted(known)}"
    )


def unaccepted(route: Route) -> list[str]:
    """Return the values the route sends that the schema does not list."""
    enums = API[f"/{route.path}"][route.method].get("enums", {})
    return [
        f"{name}={value!r}"
        for name, value in route.values.items()
        if name in enums and value not in enums[name]
    ]


@pytest.mark.parametrize("route", [route for route in ROUTES if route.values], ids=repr)
def test_the_values_are_ones_proxmox_accepts(route: Route) -> None:
    """Test a value the call fixes - a node command - is one the schema lists."""
    assert not unaccepted(route), (
        f"{route}: Proxmox does not accept {unaccepted(route)}"
    )


def test_an_annotated_action_is_read_too() -> None:
    """Test a type annotation does not take an action out of the check."""
    routes, _ = collect(
        "class Qemu:\n"
        '    start: PostAction = qemu_action("start")\n'
        '    reboot: NodeStatusCommand = NodeStatusCommand("reboot")\n'
    )
    assert [(route.method, route.path, route.values) for route in routes] == [
        ("POST", "nodes/{node}/qemu/{vmid}/status/start", {}),
        ("POST", "nodes/{node}/status", {"command": "reboot"}),
    ]


def test_a_command_proxmox_does_not_know_is_caught() -> None:
    """Test the check above would catch the bug it is there for.

    Guests were sent a `restart` Proxmox does not know; a node command is
    the same mistake one level up.
    """
    route = Route("POST", "nodes/{node}/status", {"command"}, 0, {"command": "restart"})
    assert unaccepted(route) == ["command='restart'"]
    assert any(route.values for route in ROUTES), "no fixed value was read at all"
