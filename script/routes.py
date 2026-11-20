"""Read the routes this library calls out of `endpoints.py`.

Statically, with `ast`: nothing is imported and nothing is called, so a
route can be looked at without a cluster or a client. The conformance
test holds these against Proxmox's schema, and the privilege generator
uses them to decide which checks to keep.
"""

from __future__ import annotations

import ast
from pathlib import Path

ENDPOINTS = Path(__file__).parent.parent / "aioproxmox" / "endpoints.py"

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

    def __init__(
        self,
        method: str,
        path: str,
        parameters: set[str],
        line: int,
        values: dict[str, str] | None = None,
    ) -> None:
        """Note down where the call is, so a failure can be found."""
        self.method = method
        self.path = path
        self.parameters = parameters
        self.line = line
        # The values the source fixes, `{"command": "reboot"}`.
        self.values = values or {}

    def __repr__(self) -> str:
        """Return the route as it reads in a test failure."""
        return f"{self.method} {self.path} (endpoints.py:{self.line})"


def template_of(node: ast.expr) -> str | None:
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


def parameters_of(call: ast.Call) -> tuple[set[str], dict[str, str]]:
    """Return the parameter names the call spells out, and the values it fixes."""
    names: set[str] = set()
    values: dict[str, str] = {}
    for keyword in call.keywords:
        if keyword.arg not in ("json_data", "params"):
            continue
        if not isinstance(keyword.value, ast.Dict):
            continue
        for key, value in zip(keyword.value.keys, keyword.value.values, strict=True):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    values[key.value] = value.value
    return names, values


def collect(source: str | None = None) -> tuple[list[Route], set[str]]:
    """Return every route the source spells out, and the calls it does not.

    A `request` call this cannot read - the method in a variable, the
    path passed by keyword - goes with the calls it does not spell out,
    so the test that pins those down notices it instead of the route
    silently dropping out of the check.

    Reads `endpoints.py` unless it is handed source of its own.
    """
    if source is None:
        source = ENDPOINTS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    routes: list[Route] = []
    indirect: set[str] = set()

    for node in ast.walk(tree):
        # `self.client.request("GET", f"nodes/{self.node}/status")`
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "request"
        ):
            if len(node.args) < 2 or not isinstance(node.args[0], ast.Constant):
                indirect.add(ast.unparse(node))
                continue
            path = template_of(node.args[1])
            if path is None:
                indirect.add(ast.unparse(node.args[1]))
                continue
            parameters, values = parameters_of(node)
            routes.append(
                Route(str(node.args[0].value), path, parameters, node.lineno, values)
            )
            continue

        # `start = qemu_action("start")`, annotated or not, and the node
        # commands that go through the status endpoint with a `command`.
        if (
            isinstance(node, ast.Assign | ast.AnnAssign)
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
                    Route(
                        "POST",
                        "nodes/{node}/status",
                        {"command"},
                        node.lineno,
                        {"command": action},
                    )
                )

    return routes, indirect
