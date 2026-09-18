"""Generate `aioproxmox/_privilege_data.py` from the Proxmox API schema.

For every route this library calls, the schema says which privilege
Proxmox checks before answering. That is kept as plain data next to the
code rather than looked up from the schema at runtime: 43 entries instead
of a 144 KB file in every installation.

Run `python script/generate_privileges.py` after refreshing the schema;
the test compares what it would write against what is there, so a
forgotten run fails rather than going unnoticed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from routes import collect

SCHEMA = Path(__file__).parent.parent / "schema" / "pve_api.json"
TARGET = Path(__file__).parent.parent / "aioproxmox" / "_privilege_data.py"

HEADER = '''"""What Proxmox checks before it answers - generated, do not edit.

Written by `script/generate_privileges.py` out of `schema/pve_api.json`,
which comes from Proxmox's own API schema. One entry per route this
library calls that states a check; the entry is the alternatives that
satisfy it, and each alternative is the ACL path, the privileges, and
whether one of them is enough.

    ("GET", "nodes/{node}/apt/update"): ((("/nodes/{node}", ("Sys.Modify",), False),),)

`aioproxmox.privileges` is the way to read this.
"""

CHECKS: dict[
    tuple[str, str], tuple[tuple[str, tuple[str, ...], bool], ...]
] = {
'''

Alternative = tuple[str, tuple[str, ...], bool]


class UnsupportedCheck(Exception):
    """A check shape this generator does not know how to describe."""


def alternatives(check: list[Any]) -> tuple[Alternative, ...]:
    """Return the ways one check can be satisfied.

    Proxmox writes a leaf check as `["perm", path, [privileges]]`, with
    `"any", 1` appended where one privilege is enough, and combines them
    with `["or", check, check]`. Anything else - `and`, `userid-param` and
    the other forms the schema uses elsewhere - would have to be
    described before it can be handed to a caller, so it raises instead
    of being flattened into something that reads true but is not.
    """
    if not check:
        msg = "empty check"
        raise UnsupportedCheck(msg)
    kind = check[0]
    if kind == "perm":
        path, privileges = check[1], tuple(check[2])
        any_of = "any" in check[3:]
        return ((path, privileges, any_of),)
    if kind == "or":
        found: tuple[Alternative, ...] = ()
        for part in check[1:]:
            found += alternatives(part)
        return found
    msg = f"check shape {kind!r} is not described yet: {check}"
    raise UnsupportedCheck(msg)


def entries() -> dict[tuple[str, str], tuple[Alternative, ...]]:
    """Return what the schema states for each route the library calls."""
    api: dict[str, dict[str, Any]] = json.loads(SCHEMA.read_text(encoding="utf-8"))
    routes, _ = collect()
    found: dict[tuple[str, str], tuple[Alternative, ...]] = {}
    for route in routes:
        info = api.get(f"/{route.path}", {}).get(route.method)
        if info is None or not (check := info.get("check")):
            continue
        found[route.method, route.path] = alternatives(check)
    return found


def render() -> str:
    """Return the generated module for the routes the library calls."""
    lines = [HEADER]
    for (method, path), found in sorted(entries().items()):
        lines.append(f'    ("{method}", "{path}"): (\n')
        for acl_path, privileges, any_of in found:
            names = ", ".join(f'"{privilege}"' for privilege in privileges)
            comma = "," if len(privileges) == 1 else ""
            lines.append(f'        ("{acl_path}", ({names}{comma}), {any_of}),\n')
        lines.append("    ),\n")
    lines.append("}\n")
    return "".join(lines)


def main() -> None:
    """Write the module and say how much of the API it covers."""
    text = render()
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"{TARGET}: {text.count('): (')} routes with a stated check")


if __name__ == "__main__":
    main()
