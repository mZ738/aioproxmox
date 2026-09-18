# The Proxmox API schema

Proxmox publishes its whole API as a schema: the web interface's API
viewer is driven by one file, [`apidoc.js`][apidoc], which carries every
path, the methods it answers, the parameters they take and the privileges
they check. It is the only machine-readable description of the API there
is, and this library uses it as the ground truth its hand-written
endpoints are held against.

## Why

Paths used to be read off the API viewer by hand, and hand-reading goes
wrong. `nodes/{node}/reboot` looks obvious and does not exist — a node is
rebooted through `POST nodes/{node}/status` with a `command` — and guests
were being sent a `restart` that Proxmox does not know. Both were found by
trying them against a real cluster, which is a poor way to find out.

The schema says so without asking a cluster, and it says it for all 449
paths at once, so the check costs nothing to repeat.

## What is kept, and what is not

`schema/pve_api.json` holds, per path and method:

- the names of the parameters the route takes,
- the `check` the API performs before answering it, as Proxmox writes it:
  `["perm", "/vms/{vmid}", ["VM.GuestAgent.Audit", "VM.GuestAgent.Unrestricted"], "any", 1]`.

Nothing else. Descriptions, defaults and formats stay in the
documentation the file comes from — this is a table of facts about the
API, not a copy of Proxmox's prose.

## Refreshing it

```
python script/refresh_pve_api.py
```

Run it after a Proxmox release and commit what changes. The diff then
reads as a list of what the API gained, lost or renamed, which is worth
seeing on its own.

The file is deliberately committed rather than fetched while the tests
run: `apidoc.js` is documentation, not a released artefact, so a build
should not depend on it being reachable or unchanged.

## What holds the endpoints to it

`tests/test_schema_conformance.py` reads the routes out of
`aioproxmox/endpoints.py` — statically, with `ast`, so nothing has to be
called — and checks each one against the schema:

- the path exists,
- it answers the method used,
- the parameter names visible in the call are parameters of that route.

A route the source builds elsewhere (`PostAction` keeps the path an action
factory gave it) is listed as such, and the test fails if that list
changes: a refactor that hides the paths from the reader would otherwise
quietly mute the whole file.

[apidoc]: https://pve.proxmox.com/pve-docs/api-viewer/apidoc.js
