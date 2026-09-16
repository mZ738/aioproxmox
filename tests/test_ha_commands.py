"""Tests for arming and disarming the cluster's HA stack."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aioproxmox.endpoints import ClusterEndpoint
from aioproxmox.exceptions import ProxmoxError


@pytest.mark.asyncio
async def test_arm_ha_queues_the_crm_command():
    """arm-ha is a POST under cluster/ha/status; the task id comes back."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        return_value="UPID:pve:0001:0001:1:hastart::root@pam:"
    )

    upid = await ClusterEndpoint(mock_client).arm_ha()

    assert upid.startswith("UPID:")
    mock_client.request.assert_called_with("POST", "cluster/ha/status/arm-ha")


@pytest.mark.asyncio
async def test_disarm_ha_freezes_by_default_and_takes_ignore():
    """disarm-ha needs a resource mode; freeze unless asked, ignore on request."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(return_value="UPID:x")
    cluster = ClusterEndpoint(mock_client)

    await cluster.disarm_ha()
    mock_client.request.assert_called_with(
        "POST", "cluster/ha/status/disarm-ha", json_data={"resource-mode": "freeze"}
    )
    await cluster.disarm_ha(resource_mode="ignore")
    mock_client.request.assert_called_with(
        "POST", "cluster/ha/status/disarm-ha", json_data={"resource-mode": "ignore"}
    )


@pytest.mark.asyncio
async def test_disarm_ha_refuses_an_unknown_mode():
    """Anything but freeze or ignore is refused before it reaches the cluster."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock()

    with pytest.raises(ProxmoxError):
        await ClusterEndpoint(mock_client).disarm_ha(resource_mode="panic")
    mock_client.request.assert_not_called()
