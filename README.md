# Asynchronous Python library for Proxmox products

An asynchronous, type-safe Python client tailored for extracting raw performance telemetry out of Proxmox VE clusters, nodes, and guests.

## Why another Proxmox library

This library was built out of necessity because existing ecosystem integrations and underlying client libraries have structural limitations. Whether they are synchronous bottlenecks, missing granular unnested endpoint layouts, or lacking strict runtime type enforcement. This library serves as our high-performance bridge today while we wait for any other mainstream library to catch up, open up, or accept modern async paradigms. Once they do, this phase can comfortably conclude.

## Features

* **Strict Asynchronous Execution:** Native `aiohttp` implementation designed never to block the event loop.
* **Unified REST Client:** Centralized HTTP architecture managing internal auth tickets, cookie construction, and CSRF token propagation seamlessly.
* **Granular Telemetry Models:** Distinct dataclasses separating cluster maps from deep host (`NodeStatus`), VM (`QemuStatus`), and container (`LXCStatus`) sensor profiles.
* **Smart Reconciliation:** Automated cache pruning to instantly evict vanished or decommissioned resources from tracking tables using fast set operations.

## Performance

PVE calls can be intense, the `live_test.py` script adds basic timing, especially storage is expensive. Example for a one-node cluster with only local storage:

```text
Marker / Total time / Diff. time / Progress
Timing /    0.15 ms /    0.15 ms / Init
Timing /    0.20 ms /    0.02 ms / Setup
Timing /   69.37 ms /   69.15 ms / Connect
Timing /   69.52 ms /    0.07 ms / Cluster Resources
Timing /   69.67 ms /    0.14 ms / Resource printing done
Timing /   69.72 ms /    0.04 ms / Time
Timing /   80.22 ms /   10.49 ms / Qemu List
Timing /  101.04 ms /   20.75 ms / LXC List
Timing /  101.16 ms /    0.03 ms / Qemu/LXC listing done
Timing /  105.41 ms /    4.24 ms / Node Status
Timing /  110.88 ms /    5.42 ms / VM Status
Timing /  126.18 ms /   15.24 ms / LXC
Timing /  126.33 ms /    0.06 ms / Check existing capabilities
Timing /  126.39 ms /    0.05 ms / Mark time
Timing /  130.49 ms /    4.08 ms / Permissions fetch
Timing / 1784.85 ms / 1654.30 ms / Storage
Timing / 1793.08 ms /    8.13 ms / Backups
Timing / 1793.17 ms /    0.04 ms / Time
Timing / 1793.20 ms /    0.00 ms / Guest Agent running VM start
Timing / 1944.55 ms /  151.33 ms / Guest Agent running VM complete
Timing / 1944.75 ms /    0.08 ms / Guest Agent stopped VM start
Timing / 1950.67 ms /    5.90 ms / Guest Agent stopped VM complete
Timing / 1950.75 ms /    0.03 ms / Time
```

## Usage

The library provides a modern, fluent interface built on asyncio and aiohttp, designed to mirror the structure of the Proxmox VE API while maintaining strict type safety via dataclasses.

Instead of passing endpoints via string formatting or multi-level item lookups, aioproxmox exposes clean, chainable endpoint paths. All data responses are fully validated dataclass objects rather than raw nested dictionaries.

```python
import asyncio
import aiohttp
import aioproxmox


async def main():
    async with aiohttp.ClientSession() as session:
        # Initialize the type-safe client backend
        pve = aioproxmox.ProxmoxVE(
            session=session,
            host="192.168.1.100",
            user="root@pam",
            password="your_secure_password",
            verify_ssl=False,
        )

        cluster = await pve.connect()
        print(f"Connected to cluster with: {len(cluster.resources)} resources")

        lxc_status = await pve.nodes("pvex").lxc(501).status.current()
        print(f"Container Memory: {lxc_status.mem} bytes")

        cluster_resources = await pve.cluster.resources()
        print(f"Total cluster resources tracked: {len(cluster_resources.resources)}")

        node_status = await pve.nodes("pvex").status()
        print(f"Node CPU usage: {node_status.cpu * 100}%")

        vm_status = await pve.nodes("pvex").qemu(102).status.current()
        print(f"VM {vm_status.name} Status: {vm_status.status}")


if __name__ == "__main__":
    asyncio.run(main())
```

### Contrast from Proxmoxer

| Feature | `proxmoxer` (Generic String Wrapper) | `aioproxmox` (Type-Safe Telemetry Client) |
| :--- | :--- | :--- |
| **I/O Strategy** | Synchronous (blocks the event loop) | Native Asynchronous (`aiohttp`) |
| **Path Navigation** | Verbatim endpoint strings / properties | Fluid, chainable endpoint paths |
| **Terminal Calls** | Explicit method tags required (`.get()`) | Direct execution or explicit telemetry targets |
| **Data Format** | Untyped primitives (`dict` / `list`) | Validated `Mashumaro` Dataclasses |

```python
"""Example implementation, see scripts/live_test.py for more examples."""

from proxmoxer import ProxmoxAPI

# Initialize synchronous client
proxmox = ProxmoxAPI(
    "192.168.1.100", user="root@pam", password="your_secure_password", verify_ssl=False
)

# 1. Fetch cluster resources (returns raw lists of dicts)
resources = proxmox.cluster.resources.get()
print(f"Total cluster resources tracked: {len(resources)}")

# 2. Fetch specific physical node status
node_status = proxmox.nodes("pvex").status.get()
print(f"Node CPU usage: {node_status.get('cpu', 0) * 100}%")

# 3. Fetch QEMU VM status
vm_status = proxmox.nodes("pvex").qemu(102).status.current.get()
print(f"VM Status: {vm_status.get('status')}")

# 4. Fetch LXC container status
lxc_status = proxmox.nodes("pvex").lxc(501).status.current.get()
print(f"Container Memory: {lxc_status.get('mem')} bytes")
```

## Documentation

- [The Proxmox API schema](docs/schema.md) — where `schema/pve_api.json` comes from, and how the endpoints are held against it.

## Development Diagnostics

The repository provides a diagnostic CLI utility to verify authentication mechanics, cookie/ticket attachment, and data serialization against live systems.

### Running the Live Test Script

Execute the script from the root directory by providing your targeted cluster parameters:

```bash
scripts/live_test.py <host-ip-or-fqdn> "<user@realm>" "<password>"
```
