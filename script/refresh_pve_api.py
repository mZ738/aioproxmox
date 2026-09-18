"""Refresh `schema/pve_api.json` from the Proxmox VE API schema.

The web interface's API viewer is driven by one file, `apidoc.js`, which
carries the whole API as JSON: every path, the methods it answers, the
parameters they take and the privileges they check. That is the only
machine-readable description Proxmox publishes, and it is what the
conformance test and the privilege map are built from.

Kept here are the facts a client needs - paths, methods, parameter names,
permission checks. The prose (descriptions, defaults, formats) is left
where it belongs, in the documentation the file comes from.

Run `python script/refresh_pve_api.py` after a Proxmox release and commit
what changes; the diff is then a readable list of what the API gained or
lost.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import urlopen

SOURCE = "https://pve.proxmox.com/pve-docs/api-viewer/apidoc.js"
TARGET = Path(__file__).parent.parent / "schema" / "pve_api.json"


def fetch(url: str = SOURCE) -> list[dict[str, Any]]:
    """Return the schema out of `apidoc.js`, which wraps it in one assignment."""
    with urlopen(url) as response:
        text = response.read().decode("utf-8")
    start = text.index("[")
    end = text.index("\n]\n;") + 2
    parsed: list[dict[str, Any]] = json.loads(text[start:end])
    return parsed


def flatten(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return {path: {method: {parameters, check}}} for the whole tree."""
    routes: dict[str, dict[str, Any]] = {}
    for node in nodes:
        for method, info in (node.get("info") or {}).items():
            properties = (info.get("parameters") or {}).get("properties") or {}
            entry: dict[str, Any] = {"parameters": sorted(properties)}
            if check := (info.get("permissions") or {}).get("check"):
                entry["check"] = check
            routes.setdefault(node["path"], {})[method] = entry
        routes.update(flatten(node.get("children") or []))
    return routes


def main() -> None:
    """Write the trimmed schema and say what it holds."""
    routes = flatten(fetch())
    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_text(
        json.dumps(routes, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    methods = sum(len(methods) for methods in routes.values())
    print(f"{TARGET}: {len(routes)} paths, {methods} method and path pairs")


if __name__ == "__main__":
    main()
