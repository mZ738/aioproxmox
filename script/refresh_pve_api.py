"""Refresh `schema/pve_api.json` from the Proxmox VE API schema.

The web interface's API viewer is driven by one file, `apidoc.js`, which
carries the whole API as JSON: every path, the methods it answers, the
parameters they take and the privileges they check. That is the only
machine-readable description Proxmox publishes, and it is what the
conformance test and the privilege map are built from.

Kept here are the facts a client needs - paths, methods, parameter names,
the values a parameter accepts where the schema lists them, permission
checks. The prose (descriptions, defaults, formats) is left
where it belongs, in the documentation the file comes from.

Run `python script/refresh_pve_api.py` after a Proxmox release and commit
what changes; the diff is then a readable list of what the API gained or
lost.

The extract carries a `_source` entry saying where it came from and when
the published file was last changed. `apidoc.js` itself names no version,
and the documentation site only lists past releases, so that timestamp is
the dependable answer to "is this still current": run the script, and it
says whether the published schema has moved since.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any
from urllib.request import urlopen

SOURCE = "https://pve.proxmox.com/pve-docs/api-viewer/apidoc.js"
TARGET = Path(__file__).parent.parent / "schema" / "pve_api.json"
# Where the extract records its own origin. Not a path, so everything
# reading the file skips the keys that do not start with "/".
SOURCE_KEY = "_source"


def fetch(url: str = SOURCE) -> tuple[list[dict[str, Any]], str]:
    """Return the schema out of `apidoc.js`, and when it was last published.

    The file wraps the whole API in one assignment, and the server's
    `Last-Modified` is the only version marker there is - the JavaScript
    carries none.
    """
    with urlopen(url, timeout=60) as response:
        text = response.read().decode("utf-8")
        last_modified = response.headers.get("Last-Modified", "")
    try:
        start = text.index("[")
        end = text.index("\n]\n;") + 2
    except ValueError as error:
        msg = (
            f"{url} no longer has the shape this script reads - one JSON "
            "array assigned and closed with `\\n]\\n;`. Look at the file "
            "before changing the parser."
        )
        raise ValueError(msg) from error
    parsed: list[dict[str, Any]] = json.loads(text[start:end])
    return parsed, last_modified


def on_disk() -> dict[str, Any]:
    """Return the extract as it was before this run, or nothing."""
    if not TARGET.exists():
        return {}
    parsed: dict[str, Any] = json.loads(TARGET.read_text(encoding="utf-8"))
    return parsed


def routes_of(extract: dict[str, Any]) -> dict[str, Any]:
    """Return the routes of an extract, without its `_source` entry."""
    return {path: info for path, info in extract.items() if path.startswith("/")}


def flatten(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return {path: {method: {parameters, enums, check}}} for the whole tree."""
    routes: dict[str, dict[str, Any]] = {}
    for node in nodes:
        for method, info in (node.get("info") or {}).items():
            properties = (info.get("parameters") or {}).get("properties") or {}
            entry: dict[str, Any] = {"parameters": sorted(properties)}
            # The values a parameter takes, where the schema lists them: a
            # node is sent `reboot` or `shutdown`, and nothing else.
            enums = {
                name: spec["enum"]
                for name, spec in properties.items()
                if isinstance(spec, dict) and "enum" in spec
            }
            if enums:
                entry["enums"] = enums
            # Kept as soon as the key is there: an empty check is malformed,
            # and the generator should get to refuse it rather than never
            # see it.
            permissions = info.get("permissions") or {}
            if "check" in permissions:
                entry["check"] = permissions["check"]
            routes.setdefault(node["path"], {})[method] = entry
        routes.update(flatten(node.get("children") or []))
    return routes


def main() -> None:
    """Write the trimmed schema and say what it holds."""
    before = on_disk()
    nodes, last_modified = fetch()
    routes: dict[str, Any] = dict(flatten(nodes))
    routes[SOURCE_KEY] = {
        "url": SOURCE,
        "last_modified": last_modified,
        "fetched": datetime.now(UTC).date().isoformat(),
    }
    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_text(
        json.dumps(routes, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    paths = len(routes) - 1
    methods = sum(
        len(methods) for path, methods in routes.items() if path.startswith("/")
    )
    print(f"{TARGET}: {paths} paths, {methods} method and path pairs")
    # Decided by the routes themselves: the header is a convenience the
    # server may leave out, and a file republished unchanged is no change.
    old, new = routes_of(before), routes_of(routes)
    published = (before.get(SOURCE_KEY) or {}).get("last_modified") or "unknown"
    if not old:
        print("first extract")
    elif old == new:
        print(
            f"routes unchanged (published {published} -> {last_modified or 'unknown'})"
        )
    else:
        gained = sorted(set(new) - set(old))
        lost = sorted(set(old) - set(new))
        changed = sorted(path for path in set(old) & set(new) if old[path] != new[path])
        print(
            f"routes changed: {len(gained)} paths gained, {len(lost)} lost, "
            f"{len(changed)} changed"
        )
        for mark, listed in (("+", gained), ("-", lost), ("~", changed)):
            for path in listed:
                print(f"  {mark} {path}")


if __name__ == "__main__":
    main()
