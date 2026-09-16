"""Tests for physical disks, SMART data and ZFS pools."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aioproxmox.endpoints import NodeEndpoint
from aioproxmox.exceptions import ProxmoxError
from aioproxmox.model.disks import (
    DiskSmart,
    DiskType,
    NodeDisk,
    SmartSummary,
    leading_int,
    smart_text_attributes,
)

ATA_ATTRIBUTES = [
    {"id": "9", "name": "Power_On_Hours", "raw": "20707", "value": "97"},
    {"id": "12", "name": "Power_Cycle_Count", "raw": "782", "value": "100"},
    {"id": "190", "name": "Airflow_Temperature_Cel", "raw": "38", "value": "62"},
    {
        "id": "194",
        "name": "Temperature_Celsius",
        "raw": "41 (Min/Max 20/45)",
        "value": "41",
    },
    {"id": "231", "name": "SSD_Life_Left", "raw": "0", "value": "97"},
    {"id": "174", "name": "Unexpect_Power_Loss_Ct", "raw": "127", "value": "100"},
]


NVME_TEXT = """
SMART/Health Information (NVMe Log 0x02, NSID 0x1)
Critical Warning:                   0x00
Temperature:                        43 Celsius
Available Spare:                    100%
Percentage Used:                    4%
Power Cycles:                       1,381
Power On Hours:                     3,728
Temperature Sensor 1:               43 Celsius
Temperature Sensor 2:               51 Celsius
"""


# A SAS drive behind an expander: other words for the same things, and a
# label with a colon of its own.
SAS_TEXT = """
=== START OF READ SMART DATA SECTION ===
SMART Health Status: OK

Current Drive Temperature:     27 C
Drive Trip Temperature:        85 C
Accumulated start-stop cycles:  48
Elements in grown defect list: 0
    Accumulated power on time, hours:minutes 27473:19 [1648399 minutes]
"""


def test_smart_summary_from_ata_attributes():
    """The numbered attributes: raw for most, the normalised value for life left."""
    summary = SmartSummary.from_attributes(ATA_ATTRIBUTES)

    assert summary.power_hours == 20707
    assert summary.power_cycles == 782
    assert summary.temperature == 41
    assert summary.temperature_air == 38
    assert summary.life_left == 97
    assert summary.power_loss == 127


def test_smart_summary_from_nvme_text():
    """The NVMe log: labels, thousands separators, and no confusion with sensor 1/2."""
    summary = DiskSmart(health="PASSED", type="text", text=NVME_TEXT).summary

    assert summary.temperature == 43
    assert summary.power_cycles == 1381
    assert summary.power_hours == 3728
    assert [a["name"] for a in smart_text_attributes(NVME_TEXT)] == [
        "Temperature",
        "Power Cycles",
        "Power On Hours",
    ]


def test_smart_summary_from_sas_text():
    """The SAS log, including the label that carries a colon itself."""
    summary = DiskSmart(health="OK", type="text", text=SAS_TEXT).summary

    assert summary.temperature == 27
    assert summary.power_cycles == 48
    assert summary.power_hours == 27473


def test_a_value_without_a_number_is_no_reading():
    """A virtual NVMe reporting `-` for its temperature leaves the field None."""
    summary = SmartSummary.from_attributes(
        [{"id": "194", "raw": "-"}, {"id": "9", "raw": "12h+30m"}, "nonsense"]
    )

    assert summary.temperature is None
    assert summary.power_hours == 12
    assert leading_int(True) is None
    assert leading_int(None) is None
    assert leading_int("  36 (Min/Max 20/45)") == 36


def test_a_disk_with_no_wearout_reads_none():
    """Proxmox reports `N/A` for drives without a wearout figure."""
    disk = NodeDisk.from_dict(
        {"devpath": "/dev/sdb", "size": 1, "type": "hdd", "wearout": "N/A"}
    )
    assert disk.wearout is None
    assert disk.type is DiskType.HDD
    assert (
        NodeDisk.from_dict(
            {"devpath": "/dev/sda", "size": 1, "type": "ssd", "wearout": 98}
        ).wearout
        == 98
    )
    assert (
        NodeDisk.from_dict({"devpath": "/dev/x", "size": 1, "type": "tape"}).type
        is DiskType.UNKNOWN
    )


@pytest.mark.asyncio
async def test_node_disks_endpoint(
    mock_node_disks, mock_node_disk_smart, mock_node_zfs
):
    """The three reads of the disks endpoint, with the model on top."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        side_effect=[mock_node_disks, mock_node_disk_smart, mock_node_zfs]
    )
    disks = NodeEndpoint(mock_client, "pve-01").disks()

    listed = await disks.all()
    assert [d.devpath for d in listed] == ["/dev/sda", "/dev/nvme0n1"]
    assert listed[0].type is DiskType.SSD
    assert listed[0].wearout == 98
    assert listed[1].wearout is None
    mock_client.request.assert_any_call("GET", "nodes/pve-01/disks/list")

    smart = await disks.smart("/dev/sda")
    assert smart.health == "PASSED"
    assert smart.summary.temperature == 34
    mock_client.request.assert_any_call(
        "GET", "nodes/pve-01/disks/smart", params={"disk": "/dev/sda"}
    )

    pools = await disks.zfs()
    assert pools[0].name == "rpool"
    assert pools[0].health == "ONLINE"
    assert 0.24 < pools[0].used_fraction < 0.25


@pytest.mark.asyncio
async def test_smart_rejects_a_list():
    """A list where a dict is expected is an error, not a crash later."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=[])
    with pytest.raises(ProxmoxError):
        await NodeEndpoint(mock_client, "pve-01").disks().smart("/dev/sda")
