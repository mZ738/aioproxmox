"""What Proxmox checks before it answers - generated, do not edit.

Written by `script/generate_privileges.py` out of `schema/pve_api.json`,
which comes from Proxmox's own API schema. One entry per route this
library calls that states a check; the entry is the alternatives that
satisfy it, and each alternative is the ACL path, the privileges, and
whether one of them is enough.

    ("GET", "nodes/{node}/apt/update"): ((("/nodes/{node}", ("Sys.Modify",), False),),)

`aioproxmox.privileges` is the way to read this.
"""

CHECKS: dict[tuple[str, str], tuple[tuple[str, tuple[str, ...], bool], ...]] = {
    ("GET", "cluster/backup-info/not-backed-up"): (("/", ("Sys.Audit",), False),),
    ("GET", "cluster/ceph/status"): (("/", ("Sys.Audit", "Datastore.Audit"), True),),
    ("GET", "cluster/ha/status/current"): (("/", ("Sys.Audit",), False),),
    ("GET", "cluster/status"): (("/", ("Sys.Audit",), False),),
    ("GET", "nodes/{node}/apt/update"): (("/nodes/{node}", ("Sys.Modify",), False),),
    ("GET", "nodes/{node}/disks/list"): (
        ("/", ("Sys.Audit",), False),
        ("/nodes/{node}", ("Sys.Audit",), False),
    ),
    ("GET", "nodes/{node}/disks/smart"): (("/", ("Sys.Audit",), False),),
    ("GET", "nodes/{node}/disks/zfs"): (("/", ("Sys.Audit",), False),),
    ("GET", "nodes/{node}/lxc/{vmid}/interfaces"): (
        ("/vms/{vmid}", ("VM.Audit",), False),
    ),
    ("GET", "nodes/{node}/lxc/{vmid}/snapshot"): (
        ("/vms/{vmid}", ("VM.Audit",), False),
    ),
    ("GET", "nodes/{node}/lxc/{vmid}/status/current"): (
        ("/vms/{vmid}", ("VM.Audit",), False),
    ),
    ("GET", "nodes/{node}/qemu/{vmid}/agent/get-fsinfo"): (
        ("/vms/{vmid}", ("VM.GuestAgent.Audit", "VM.GuestAgent.Unrestricted"), True),
    ),
    ("GET", "nodes/{node}/qemu/{vmid}/agent/network-get-interfaces"): (
        ("/vms/{vmid}", ("VM.GuestAgent.Audit", "VM.GuestAgent.Unrestricted"), True),
    ),
    ("GET", "nodes/{node}/qemu/{vmid}/snapshot"): (
        ("/vms/{vmid}", ("VM.Audit",), False),
    ),
    ("GET", "nodes/{node}/qemu/{vmid}/status/current"): (
        ("/vms/{vmid}", ("VM.Audit",), False),
    ),
    ("GET", "nodes/{node}/status"): (("/nodes/{node}", ("Sys.Audit",), False),),
    ("POST", "cluster/ha/status/arm-ha"): (("/", ("Sys.Console",), False),),
    ("POST", "cluster/ha/status/disarm-ha"): (("/", ("Sys.Console",), False),),
    ("POST", "nodes/{node}/lxc/{vmid}/snapshot"): (
        ("/vms/{vmid}", ("VM.Snapshot",), False),
    ),
    ("POST", "nodes/{node}/lxc/{vmid}/status/reboot"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/lxc/{vmid}/status/resume"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/lxc/{vmid}/status/shutdown"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/lxc/{vmid}/status/start"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/lxc/{vmid}/status/stop"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/lxc/{vmid}/status/suspend"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/qemu/{vmid}/agent/ping"): (
        ("/vms/{vmid}", ("VM.GuestAgent.Audit", "VM.GuestAgent.Unrestricted"), True),
    ),
    ("POST", "nodes/{node}/qemu/{vmid}/snapshot"): (
        ("/vms/{vmid}", ("VM.Snapshot",), False),
    ),
    ("POST", "nodes/{node}/qemu/{vmid}/status/reboot"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/qemu/{vmid}/status/reset"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/qemu/{vmid}/status/resume"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/qemu/{vmid}/status/shutdown"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/qemu/{vmid}/status/start"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/qemu/{vmid}/status/stop"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/qemu/{vmid}/status/suspend"): (
        ("/vms/{vmid}", ("VM.PowerMgmt",), False),
    ),
    ("POST", "nodes/{node}/status"): (("/nodes/{node}", ("Sys.PowerMgmt",), False),),
    ("POST", "nodes/{node}/wakeonlan"): (("/nodes/{node}", ("Sys.PowerMgmt",), False),),
    ("PUT", "nodes/{node}/lxc/{vmid}/config"): (
        (
            "/vms/{vmid}",
            (
                "VM.Config.Disk",
                "VM.Config.CPU",
                "VM.Config.Memory",
                "VM.Config.Network",
                "VM.Config.Options",
            ),
            True,
        ),
    ),
    ("PUT", "nodes/{node}/qemu/{vmid}/config"): (
        (
            "/vms/{vmid}",
            (
                "VM.Config.Disk",
                "VM.Config.CDROM",
                "VM.Config.CPU",
                "VM.Config.Memory",
                "VM.Config.Network",
                "VM.Config.HWType",
                "VM.Config.Options",
                "VM.Config.Cloudinit",
            ),
            True,
        ),
    ),
}
