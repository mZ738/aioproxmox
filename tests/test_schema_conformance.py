"""Check every route in `endpoints.py` against Proxmox's own API schema.

Paths were read off the API viewer by hand, and hand-reading goes wrong:
`nodes/{node}/reboot` looked obvious and does not exist (a node is
rebooted through `nodes/{node}/status` with a `command`), and guests were
sent a `restart` that Proxmox does not know. Both were only found by
trying them against a real cluster.

Proxmox publishes the whole API as a schema, so this does not have to be
found by trying: the endpoints are read out of the source and compared
with `schema/pve_api.json` - path, method and the parameter names that
are visible in the call.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

ENDPOINTS = Path(__file__).parent.parent / "aioproxmox" / "endpoints.py"
SCHEMA = Path(__file__).parent.parent / "schema" / "pve_api.json"

# The path an action factory builds, with ACTION for the action it is given.
ACTION = "\x00"
FACTORIES = {
    "node_action": f"nodes/{{node}}/{ACTION}",
    "qemu_action": f"nodes/{{node}}/qemu/{{vmid}}/status/{ACTION}",
    "lxc_action": f"nodes/{{node}}/lxc/{{vmid}}/status/{ACTION}",
}
# What a placeholder in an f-string stands for, whichever way the
# endpoint holds it: `self.node`, `instance.node` or a local `node`.
PLACEHOLDERS = {"node": "{node}", "vmid": "{vmid}", "storage": "{storage}"}
# Expressions that stand for a path built elsewhere - `PostAction` keeps
# the path the factories above gave it. Their routes are found through
# the factory, so the call itself has nothing left to check.
INDIRECT = {"self.path"}


class Route:
    """One call the library makes: method, path template, visible parameters."""

    def __init__(self, method: str, path: str, parameters: set[str], line: int) -> None:
        """Note down where the call is, so a failure can be found."""
        self.method = method
        self.path = path
        self.parameters = parameters
        self.line = line

    def __repr__(self) -> str:
        """Return the route as it reads in a test failure."""
        return f"{self.method} {self.path} (endpoints.py:{self.line})"


def _template(node: ast.expr) -> str | None:
    """Return the path an argument spells out, or None when it is built elsewhere."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if not isinstance(node, ast.JoinedStr):
        return None
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(str(value.value))
            continue
        if not isinstance(value, ast.FormattedValue):
            return None
        name = value.value
        attribute = name.attr if isinstance(name, ast.Attribute) else None
        plain = name.id if isinstance(name, ast.Name) else None
        placeholder = PLACEHOLDERS.get(attribute or plain or "")
        if placeholder is None:
            # A template of a template, as in the action descriptors.
            return None
        parts.append(placeholder)
    return "".join(parts)


def _parameters(call: ast.Call) -> set[str]:
    """Return the parameter names the call spells out, if it spells any out."""
    names: set[str] = set()
    for keyword in call.keywords:
        if keyword.arg not in ("json_data", "params"):
            continue
        if not isinstance(keyword.value, ast.Dict):
            continue
        for key in keyword.value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
    return names


def _collect() -> tuple[list[Route], set[str]]:
    """Return every route the source spells out, and the calls it does not."""
    tree = ast.parse(ENDPOINTS.read_text(encoding="utf-8"))
    routes: list[Route] = []
    indirect: set[str] = set()

    for node in ast.walk(tree):
        # `self.client.request("GET", f"nodes/{self.node}/status")`
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "request"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
        ):
            path = _template(node.args[1])
            if path is None:
                indirect.add(ast.unparse(node.args[1]))
                continue
            routes.append(
                Route(
                    str(node.args[0].value),
                    path,
                    _parameters(node),
                    node.lineno,
                )
            )
            continue

        # `start = qemu_action("start")`, and the node commands that go
        # through the status endpoint with a `command`.
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.args
            and isinstance(node.value.args[0], ast.Constant)
        ):
            factory = node.value.func.id
            action = str(node.value.args[0].value)
            if template := FACTORIES.get(factory):
                routes.append(
                    Route("POST", template.replace(ACTION, action), set(), node.lineno)
                )
            elif factory == "NodeStatusCommand":
                routes.append(
                    Route("POST", "nodes/{node}/status", {"command"}, node.lineno)
                )

    return routes, indirect


def _schema() -> dict[str, dict[str, Any]]:
    """Return the trimmed Proxmox API schema."""
    parsed: dict[str, dict[str, Any]] = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return parsed


ROUTES, INDIRECT_CALLS = _collect()
API = _schema()


def test_the_endpoints_are_read_out_of_the_source() -> None:
    """Test the reading itself still works, so a refactor cannot mute this file."""
    assert len(ROUTES) > 50
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
