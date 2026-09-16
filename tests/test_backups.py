"""Tests for a node's last and running backup, read from its task log."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from aioproxmox.endpoints import NodeEndpoint
from aioproxmox.model.pve import NodeTask
from aioproxmox.model.summary import LastBackup, RunningBackup


def _task(**overrides) -> NodeTask:
    base = {
        "id": "100,101",
        "node": "pve",
        "pid": 1,
        "pstart": 1,
        "starttime": 1_757_900_000,
        "type": "vzdump",
        "upid": "UPID:pve:00000001:00000001:69554B80:vzdump:100,101:root@pam:",
        "user": "root@pam",
        "endtime": 1_757_901_800,
        "status": "OK",
    }
    return NodeTask.from_dict({**base, **overrides})


def test_last_backup_from_the_archive():
    """Newest first; duration from the two timestamps; `job errors` is not OK."""
    last = LastBackup.from_tasks([_task(), _task(starttime=1, endtime=2)])
    assert last is not None
    assert last.duration == 1800
    assert last.ok is True
    assert last.guests == "100,101"
    assert last.user == "root@pam"

    partial = LastBackup.from_tasks([_task(status="job errors", id="")])
    assert partial.ok is False
    assert partial.guests is None  # everything on the node
    assert LastBackup.from_tasks([_task(type="qmstart")]) is None
    assert LastBackup.from_tasks([_task(endtime=None)]).duration is None


def test_running_backup_from_the_active_list():
    """A vzdump in progress: since when and for which guests; nothing means None."""
    running = RunningBackup.from_tasks([_task(endtime=None, status=None, id="101")])
    assert running is not None
    assert running.guests == "101"
    assert running.since == datetime.fromtimestamp(1_757_900_000, tz=UTC)
    assert RunningBackup.from_tasks([]) is None


@pytest.mark.asyncio
async def test_tasks_takes_a_source():
    """`source=active` reaches the query string."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value=[])
    await NodeEndpoint(mock_client, "pve-01").tasks(
        typefilter="vzdump", limit=1, source="active"
    )
    mock_client.request.assert_called_with(
        "GET",
        "nodes/pve-01/tasks",
        params={"typefilter": "vzdump", "limit": 1, "source": "active"},
    )
