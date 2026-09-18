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

import json
from pathlib import Path
from typing import Any

import pytest

from script.routes import INDIRECT, Route, collect

SCHEMA = Path(__file__).parent.parent / "schema" / "pve_api.json"


def _schema() -> dict[str, dict[str, Any]]:
    """Return the trimmed Proxmox API schema."""
    parsed: dict[str, dict[str, Any]] = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return parsed


ROUTES, INDIRECT_CALLS = collect()
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
