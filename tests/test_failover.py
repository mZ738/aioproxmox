"""Tests for moving to another node of the cluster when the configured host is gone."""

import asyncio
import ssl
import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from aiohttp.client_reqrep import ConnectionKey
import pytest

from aioproxmox import ProxmoxHTTPAuth, ProxmoxVE
from aioproxmox.const import PROBE_TIMEOUT
from aioproxmox.endpoints import ClusterEndpoint
from aioproxmox.exceptions import ProxmoxConnectionError, ProxmoxSSLError


def _client_with_session() -> tuple[ProxmoxVE, AsyncMock]:
    session = AsyncMock(spec=aiohttp.ClientSession)
    login = AsyncMock()
    login.status = 200
    login.json.return_value = {"data": {"ticket": "t2", "CSRFPreventionToken": "c2"}}
    session.post.return_value.__aenter__.return_value = login
    pve = ProxmoxVE(
        session=session, host="192.0.2.1", user="root@pam", password="secret"
    )
    assert isinstance(pve.auth, ProxmoxHTTPAuth)
    pve.auth.birth_time = time.monotonic()
    pve.auth.pve_auth_ticket = "t1"
    return pve, session


def _response(status: int, payload: object = None, text: str = "") -> AsyncMock:
    response = AsyncMock()
    response.status = status
    response.json.return_value = {"data": payload}
    response.text.return_value = text
    return response


def _connection_key() -> ConnectionKey:
    return ConnectionKey._make(
        {"host": "192.0.2.1", "port": 8006}.get(field)
        for field in ConnectionKey._fields
    )


def _never_connected() -> aiohttp.ClientConnectorError:
    """What aiohttp raises where no connection was made, so nothing was sent."""
    return aiohttp.ClientConnectorError(
        _connection_key(), OSError(111, "Connection refused")
    )


def _answering(pve: ProxmoxVE, session: AsyncMock, answers: list[object]) -> list[str]:
    """Hand out `answers` in order, noting the host the client is on for each."""
    in_use: list[str] = []
    pending = iter(answers)

    def answer() -> object:
        in_use.append(pve.host)
        result = next(pending)
        if isinstance(result, Exception):
            raise result
        return result

    session.request.return_value.__aenter__.side_effect = answer
    return in_use


@pytest.mark.asyncio
async def test_failover_moves_to_the_next_host_that_answers():
    """The configured host is gone; the second candidate answers `version`."""
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # the request on host 1
        aiohttp.ClientConnectionError("refused"),  # host 1, asked whether it is there
        aiohttp.ClientConnectionError("refused"),  # version probe on host 2
        _response(200, {"version": "9.2"}),  # version probe on host 3
        _response(200, {"ok": 1}),  # the request, repeated on host 3
    ]

    assert await pve.request("GET", "nodes") == {"ok": 1}
    assert pve.host == "192.0.2.3"
    assert pve.base_url == "https://192.0.2.3:8006/api2/json"
    assert pve.auth.base_url == pve.base_url
    urls = [call.kwargs["url"] for call in session.request.call_args_list]
    assert urls[-1] == "https://192.0.2.3:8006/api2/json/nodes"


@pytest.mark.asyncio
async def test_a_renewal_meeting_a_dead_host_fails_over_too():
    """The hourly ticket renewal is the first thing to hit a host that went away.

    A password client renews its ticket before a request once the ticket is an
    hour old. That renewal used to run outside the failover, so a dead host was
    reported instead of left for another node - for every request from then on.
    """
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2"])
    pve.auth.birth_time = time.monotonic() - 2 * pve.auth.renew_age
    login = AsyncMock()
    login.status = 200
    login.json.return_value = {"data": {"ticket": "t2", "CSRFPreventionToken": "c2"}}
    session.post.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # renewal on host 1
        login,  # renewal on host 2
    ]
    session.request.return_value.__aenter__.side_effect = [
        _response(200, {"version": "9.2"}),  # version probe on host 2
        _response(200, {"ok": 1}),  # the request on host 2
    ]

    assert await pve.request("GET", "nodes") == {"ok": 1}
    assert pve.host == "192.0.2.2"
    assert pve.auth.pve_auth_ticket == "t2"
    renewals = [call.args[0] for call in session.post.call_args_list]
    assert renewals == [
        "https://192.0.2.1:8006/api2/json/access/ticket",
        "https://192.0.2.2:8006/api2/json/access/ticket",
    ]


@pytest.mark.asyncio
async def test_a_burst_of_renewals_moves_once():
    """Every request of a burst renews the old ticket, and every renewal fails.

    The renewal has its proof and does not ask the host again - so without
    saying which host it failed on, each one took another step around the
    cluster, a dead host's probe included, one after the other.
    """
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3"])
    pve.auth.birth_time = time.monotonic() - 2 * pve.auth.renew_age
    login = _response(200, {"ticket": "t2", "CSRFPreventionToken": "c2"})

    async def renewal() -> AsyncMock:
        attempt = session.post.call_count
        await asyncio.sleep(0)  # the other request gets as far as this one
        if attempt <= 2:
            raise aiohttp.ClientConnectionError("refused")  # both, on host 1
        return login

    session.post.return_value.__aenter__.side_effect = renewal
    session.request.return_value.__aenter__.side_effect = [
        _response(200, {"version": "9.2"}),  # host 2 answers, asked once
        _response(200, {"ok": 1}),  # one request there
        _response(200, {"ok": 1}),  # and the other
    ]

    answers = await asyncio.gather(
        pve.request("GET", "nodes"), pve.request("GET", "nodes")
    )

    assert answers == [{"ok": 1}, {"ok": 1}]
    assert pve.host == "192.0.2.2"
    urls = [call.kwargs["url"] for call in session.request.call_args_list]
    assert urls == [
        "https://192.0.2.2:8006/api2/json/version",
        "https://192.0.2.2:8006/api2/json/nodes",
        "https://192.0.2.2:8006/api2/json/nodes",
    ]


@pytest.mark.asyncio
async def test_without_another_host_the_failure_is_reported():
    """A single host has nowhere to go; the connection error reaches the caller."""
    pve, session = _client_with_session()
    session.request.return_value.__aenter__.side_effect = aiohttp.ClientConnectionError(
        "refused"
    )

    with pytest.raises(ProxmoxConnectionError):
        await pve.request("GET", "nodes")
    assert pve.hosts == ("192.0.2.1",)


@pytest.mark.asyncio
async def test_learn_hosts_from_cluster_status():
    """Every node's corosync address except the local one is remembered, once."""
    pve, session = _client_with_session()
    session.request.return_value.__aenter__.return_value = _response(
        200,
        [
            {"type": "cluster", "name": "lab", "nodes": 3},
            {"type": "node", "name": "pve1", "ip": "192.0.2.1", "local": 1},
            {"type": "node", "name": "pve2", "ip": "192.0.2.2", "local": 0},
            {"type": "node", "name": "pve3", "ip": "192.0.2.3"},
        ],
    )

    await pve.learn_hosts_from_cluster()
    await pve.learn_hosts_from_cluster()

    assert pve.hosts == ("192.0.2.1", "192.0.2.2", "192.0.2.3")


@pytest.mark.asyncio
async def test_learning_hosts_survives_a_refused_status_read():
    """cluster/status needs no privilege, but a 403 still must not break setup."""
    pve, session = _client_with_session()
    session.request.return_value.__aenter__.return_value = _response(403, text="denied")

    await pve.learn_hosts_from_cluster()

    assert pve.hosts == ("192.0.2.1",)


@pytest.mark.asyncio
async def test_cluster_status_lists_the_nodes():
    """cluster/status: the cluster entry and one entry per node, dicts only."""
    mock_client = MagicMock()
    mock_client.request = AsyncMock(
        return_value=[
            {"type": "cluster", "name": "lab"},
            {"type": "node", "name": "pve1", "ip": "192.0.2.1", "local": 1},
            "nonsense",
        ]
    )
    entries = await ClusterEndpoint(mock_client).status()
    assert [entry["type"] for entry in entries] == ["cluster", "node"]
    mock_client.request.assert_called_with("GET", "cluster/status")


@pytest.mark.asyncio
async def test_a_request_for_a_node_that_is_down_keeps_the_host():
    """A host that answers is kept, whatever the request ran into.

    A path names a node, and the host forwards what is not its own. With
    that node down, pveproxy fails the connection - which used to read as
    "this host stopped answering", so the client walked the cluster,
    repeating the same doomed request on every node. On a cluster of two
    the walk lands on the node that is down.
    """
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("no route to host"),  # the request
        _response(200, {"version": "9.2"}),  # the host itself is fine
    ]

    with pytest.raises(ProxmoxConnectionError):
        await pve.request("GET", "nodes/pve-04/lxc/505/status/current")

    assert pve.host == "192.0.2.1"
    assert pve.base_url == "https://192.0.2.1:8006/api2/json"


@pytest.mark.asyncio
async def test_the_second_request_to_meet_a_dead_host_does_not_switch_again():
    """Once one request has moved the client, the next one stays put.

    Every consumer polls on its own, so several requests hit the dead host
    at once. The first moves; the rest find a host that answers and leave
    it alone instead of each taking another step around the cluster.
    """
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # first request
        aiohttp.ClientConnectionError("refused"),  # host 1, asked and silent
        _response(200, {"version": "9.2"}),  # host 2 answers
        _response(200, {"ok": 1}),  # first request, repeated
        aiohttp.ClientConnectionError("refused"),  # second request
        _response(200, {"version": "9.2"}),  # host 2 still answers
    ]

    assert await pve.request("GET", "nodes") == {"ok": 1}
    assert pve.host == "192.0.2.2"

    with pytest.raises(ProxmoxConnectionError):
        await pve.request("GET", "nodes/pve-04/status/current")

    assert pve.host == "192.0.2.2"


@pytest.mark.asyncio
async def test_a_probe_does_not_wait_out_the_whole_timeout():
    """The probe is given its own, shorter timeout.

    It runs after a request has already waited out `timeout`, and a cluster
    of four would otherwise spend a minute of probes before giving up.
    """
    pve, session = _client_with_session()
    pve.timeout = 30.0
    pve.learn_hosts(["192.0.2.2"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # the request
        aiohttp.ClientConnectionError("refused"),  # host 1 is really gone
        _response(200, {"version": "9.2"}),  # host 2 answers
        _response(200, {"ok": 1}),  # the request, repeated
    ]

    assert await pve.request("GET", "nodes") == {"ok": 1}

    timeouts = [call.kwargs["timeout"].total for call in session.request.call_args_list]
    assert timeouts == [30.0, PROBE_TIMEOUT, PROBE_TIMEOUT, 30.0]


@pytest.mark.asyncio
async def test_the_second_request_repeats_where_the_first_moved_to():
    """A request that failed on the host another one has already left.

    Every consumer polls on its own, so a dead host is met by several
    requests at once. The first moves the client; the rest failed on a
    host that is no longer the one in use, and are simply repeated on the
    new one - instead of each taking another step around the cluster, or
    failing for a cycle because the new host answers the probe.
    """
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # the first request
        aiohttp.ClientConnectionError("refused"),  # host 1 is really gone
        _response(200, {"version": "9.2"}),  # host 2 answers
        _response(200, {"first": 1}),  # the first request, repeated
    ]

    assert await pve.request("GET", "nodes") == {"first": 1}
    assert pve.host == "192.0.2.2"
    asked = session.request.call_count

    assert await pve.failover(from_host="192.0.2.1") is True

    assert pve.host == "192.0.2.2"
    # Nothing was asked of anyone: the switch had already happened.
    assert session.request.call_count == asked


@pytest.mark.asyncio
async def test_a_host_that_refuses_the_credentials_is_still_a_host():
    """A 401 is an answer: the host is there, it only wants a ticket.

    `version` needs authentication, and before a password login there is
    no ticket to send - so every healthy candidate replies 401. Counting
    that as silence left a client whose host had gone unable to log in
    anywhere else: the fallback found nothing and setup failed with
    "connection is unreachable" for as long as the node stayed down.
    """
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2"])
    session.post.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # the login on host 1
        _response(200, {"ticket": "t2", "CSRFPreventionToken": "c2"}),  # on host 2
    ]
    session.request.return_value.__aenter__.side_effect = [
        _response(401, text="no ticket"),  # host 2, asked without a ticket
        _response(200, {"ok": 1}),  # the request there
    ]
    pve.auth.birth_time = time.monotonic() - 2 * pve.auth.renew_age

    assert await pve.request("GET", "nodes") == {"ok": 1}
    assert pve.host == "192.0.2.2"


@pytest.mark.asyncio
async def test_a_fresh_membership_list_replaces_the_peers():
    """A cluster that lost a node does not keep it as a fallback for good.

    Suggested for the Home Assistant integration by Nick Triantos: after a
    cluster is reduced, or split into standalone servers, an address that
    once belonged to a peer can by then answer for a machine of its own.
    """
    pve, _session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3"])

    pve.learn_hosts(["192.0.2.3"], replace=True)

    assert pve.hosts == ("192.0.2.1", "192.0.2.3")
    assert pve.host == "192.0.2.1"


@pytest.mark.asyncio
async def test_replacing_keeps_known_hosts_in_their_place():
    """The cluster names its nodes in a different order every time it is asked.

    The order here is the order the hosts are tried in, and what a caller
    stores. Neither should be reshuffled by the same nodes coming back in
    another order.
    """
    pve, _session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3", "192.0.2.4"], replace=True)

    pve.learn_hosts(["192.0.2.4", "192.0.2.2", "192.0.2.3"], replace=True)

    assert pve.hosts == ("192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4")

    # One node gone and one new: the others stay where they were.
    pve.learn_hosts(["192.0.2.5", "192.0.2.4", "192.0.2.2"], replace=True)

    assert pve.hosts == ("192.0.2.1", "192.0.2.2", "192.0.2.4", "192.0.2.5")
    assert pve.host == "192.0.2.1"


@pytest.mark.asyncio
async def test_replacing_keeps_the_host_in_use():
    """The address being talked to stays, named by the cluster or not.

    Dropping it would leave the client pointing at a host it no longer
    admits to knowing, and the index would move under the request in
    flight.
    """
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # the request
        aiohttp.ClientConnectionError("refused"),  # host 1 is gone
        _response(200, {"version": "9.2"}),  # host 2 answers
        _response(200, {"ok": 1}),  # the request, repeated
    ]
    assert await pve.request("GET", "nodes") == {"ok": 1}
    assert pve.host == "192.0.2.2"

    # A membership list that knows neither the configured host's peer nor
    # the host in use.
    pve.learn_hosts(["192.0.2.9"], replace=True)

    assert pve.host == "192.0.2.2"
    assert pve.hosts == ("192.0.2.1", "192.0.2.9", "192.0.2.2")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broke",
    [aiohttp.ServerDisconnectedError(), TimeoutError()],
    ids=["disconnected", "timed out"],
)
async def test_a_command_that_may_have_arrived_is_not_sent_again(broke: Exception):
    """A connection that broke after it was made may have delivered the command.

    Proxmox can hold the `shutdown` already when its answer is lost. With
    the host silent afterwards the client moves - but sending the command
    again on the next node would run it twice, so the error is raised and
    the caller decides.
    """
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2"])
    session.request.return_value.__aenter__.side_effect = [
        broke,  # the command: sent, and then nothing
        aiohttp.ClientConnectionError("refused"),  # host 1 is gone
        _response(200, {"version": "9.2"}),  # host 2 answers
        _response(200, "UPID:pve2:shutdown"),  # the command a second time, if sent
    ]

    with pytest.raises(ProxmoxConnectionError):
        await pve.request("POST", "nodes/pve/qemu/100/status/shutdown")

    assert pve.host == "192.0.2.2"
    methods = [call.kwargs["method"] for call in session.request.call_args_list]
    assert methods == ["POST", "GET", "GET"]


@pytest.mark.asyncio
async def test_a_read_is_repeated_whatever_broke():
    """A read changes nothing, so it is repeated on the new host every time."""
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ServerDisconnectedError(),  # the read, cut off
        aiohttp.ClientConnectionError("refused"),  # host 1 is gone
        _response(200, {"version": "9.2"}),  # host 2 answers
        _response(200, {"ok": 1}),  # the read, repeated
    ]

    assert await pve.request("GET", "nodes") == {"ok": 1}
    assert pve.host == "192.0.2.2"


@pytest.mark.asyncio
async def test_a_command_that_never_left_is_sent_on_the_next_host():
    """Where no connection was made nothing was sent, and repeating is safe."""
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2"])
    session.request.return_value.__aenter__.side_effect = [
        _never_connected(),  # the command, to a host that is not there
        _never_connected(),  # host 1, asked and silent
        _response(200, {"version": "9.2"}),  # host 2 answers
        _response(200, "UPID:pve2:shutdown"),  # the command, on host 2
    ]

    task = await pve.request("POST", "nodes/pve/qemu/100/status/shutdown")

    assert task == "UPID:pve2:shutdown"
    urls = [call.kwargs["url"] for call in session.request.call_args_list]
    assert urls[-1] == (
        "https://192.0.2.2:8006/api2/json/nodes/pve/qemu/100/status/shutdown"
    )


@pytest.mark.asyncio
async def test_a_candidate_is_asked_without_moving_the_client():
    """Until a candidate has answered, the client stays where it is.

    Other requests do not wait for the lock. Pointing the whole client at
    each candidate in turn sent them to a host nobody had heard from yet -
    a dead one, for as long as its probe took.
    """
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3"])
    in_use = _answering(
        pve,
        session,
        [
            aiohttp.ClientConnectionError("refused"),  # the request on host 1
            aiohttp.ClientConnectionError("refused"),  # host 1, asked and silent
            aiohttp.ClientConnectionError("refused"),  # host 2, silent too
            _response(200, {"version": "9.2"}),  # host 3 answers
            _response(200, {"ok": 1}),  # the request, repeated on host 3
        ],
    )

    assert await pve.request("GET", "nodes") == {"ok": 1}

    urls = [call.kwargs["url"] for call in session.request.call_args_list]
    assert urls == [
        "https://192.0.2.1:8006/api2/json/nodes",
        "https://192.0.2.1:8006/api2/json/version",
        "https://192.0.2.2:8006/api2/json/version",
        "https://192.0.2.3:8006/api2/json/version",
        "https://192.0.2.3:8006/api2/json/nodes",
    ]
    assert in_use == ["192.0.2.1"] * 4 + ["192.0.2.3"]
    assert pve.auth.base_url == "https://192.0.2.3:8006/api2/json"


@pytest.mark.asyncio
async def test_with_nobody_answering_the_client_never_left():
    """A round that finds no one leaves the client on the host it was on."""
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3"])
    in_use = _answering(pve, session, [aiohttp.ClientConnectionError("refused")] * 4)

    with pytest.raises(ProxmoxConnectionError):
        await pve.request("GET", "nodes")

    assert in_use == ["192.0.2.1"] * 4
    assert pve.base_url == "https://192.0.2.1:8006/api2/json"


@pytest.mark.asyncio
async def test_a_host_dropped_while_it_was_asked_is_not_moved_to():
    """The list may be replaced under a round of probes; the round follows it."""
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3"])
    answers = iter(
        [
            aiohttp.ClientConnectionError("refused"),  # the request on host 1
            aiohttp.ClientConnectionError("refused"),  # host 1, asked and silent
            _response(200, {"version": "9.2"}),  # host 2 answers, but too late
            _response(200, {"version": "9.2"}),  # host 3 answers
            _response(200, {"ok": 1}),  # the request, repeated on host 3
        ]
    )

    def answer() -> object:
        if session.request.call_count == 3:
            # While host 2 is asked, the cluster stops naming it.
            pve.learn_hosts(["192.0.2.3"], replace=True)
        result = next(answers)
        if isinstance(result, Exception):
            raise result
        return result

    session.request.return_value.__aenter__.side_effect = answer

    assert await pve.request("GET", "nodes") == {"ok": 1}
    assert pve.hosts == ("192.0.2.1", "192.0.2.3")
    assert pve.host == "192.0.2.3"


@pytest.mark.asyncio
async def test_a_probe_never_waits_longer_than_a_request():
    """A client whose own timeout is the shorter one uses that for the probe.

    The default is: 5 s against the probe's 10. A probe that outlasts the
    request it follows is not the short question it was meant to be.
    """
    pve, session = _client_with_session()
    assert pve.timeout < PROBE_TIMEOUT
    pve.learn_hosts(["192.0.2.2"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # the request
        aiohttp.ClientConnectionError("refused"),  # host 1 is really gone
        _response(200, {"version": "9.2"}),  # host 2 answers
        _response(200, {"ok": 1}),  # the request, repeated
    ]

    assert await pve.request("GET", "nodes") == {"ok": 1}

    timeouts = [call.kwargs["timeout"].total for call in session.request.call_args_list]
    assert timeouts == [pve.timeout] * 4


@pytest.mark.asyncio
async def test_the_first_login_leaves_a_host_that_is_not_there():
    """`connect()` with peers kept from an earlier session, the configured host off.

    The login is not a `request()`, so it used to raise with a list of
    working nodes at hand.
    """
    session = AsyncMock(spec=aiohttp.ClientSession)
    pve = ProxmoxVE(
        session=session, host="192.0.2.1", user="root@pam", password="secret"
    )
    pve.learn_hosts(["192.0.2.2"])
    session.post.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # the login on host 1
        _response(200, {"ticket": "t1", "CSRFPreventionToken": "c1"}),  # on host 2
    ]
    session.request.return_value.__aenter__.side_effect = [
        _response(401, text="no ticket"),  # host 2, asked without a ticket
        _response(200, []),  # cluster/resources there
    ]

    await pve.connect()

    assert pve.host == "192.0.2.2"
    logins = [call.args[0] for call in session.post.call_args_list]
    assert logins == [
        "https://192.0.2.1:8006/api2/json/access/ticket",
        "https://192.0.2.2:8006/api2/json/access/ticket",
    ]


@pytest.mark.asyncio
async def test_the_first_login_with_nowhere_to_go_reports_the_failure():
    """Without a list, `connect()` raises what the login raised."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    pve = ProxmoxVE(
        session=session, host="192.0.2.1", user="root@pam", password="secret"
    )
    session.post.return_value.__aenter__.side_effect = aiohttp.ClientConnectionError(
        "refused"
    )

    with pytest.raises(ProxmoxConnectionError):
        await pve.connect()

    session.request.assert_not_called()


@pytest.mark.asyncio
async def test_a_learned_host_is_kept_once_however_it_is_spelled():
    """Hosts are compared the way the configured one is stored."""
    pve, _session = _client_with_session()

    pve.learn_hosts(["192.0.2.1:8006", "192.0.2.2:8006", "192.0.2.2"])

    assert pve.hosts == ("192.0.2.1", "192.0.2.2")


@pytest.mark.asyncio
async def test_a_bare_ipv6_address_is_its_bracketed_self():
    """`cluster/status` gives an IPv6 address bare; the client keeps it bracketed."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    pve = ProxmoxVE(
        session=session, host="2001:db8::1", user="root@pam", password="secret"
    )

    pve.learn_hosts(["2001:db8::1", "2001:db8::2"])
    pve.learn_hosts(["2001:db8::2", "[2001:db8::2]"], replace=True)

    assert pve.hosts == ("[2001:db8::1]", "[2001:db8::2]")


@pytest.mark.asyncio
async def test_a_certificate_that_does_not_verify_is_reported_not_left():
    """A broken certificate is not a host that is gone.

    The host is there and says who it is; that the client does not believe
    it is for the user to hear about, and the node next to it is no answer.
    """
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectorCertificateError(
            _connection_key(), ssl.SSLCertVerificationError("self-signed certificate")
        ),
        _response(200, {"version": "9.2"}),  # host 2, if it were asked
        _response(200, {"ok": 1}),  # the request there, if it were sent
    ]

    with pytest.raises(ProxmoxSSLError):
        await pve.request("GET", "nodes")

    assert pve.host == "192.0.2.1"
    assert session.request.call_count == 1


@pytest.mark.asyncio
async def test_a_candidate_whose_certificate_does_not_verify_is_passed_over():
    """With verification on, a fallback has to present a certificate that holds."""
    pve, session = _client_with_session()
    pve.learn_hosts(["192.0.2.2", "192.0.2.3"])
    session.request.return_value.__aenter__.side_effect = [
        aiohttp.ClientConnectionError("refused"),  # the request on host 1
        aiohttp.ClientConnectionError("refused"),  # host 1 is gone
        aiohttp.ClientConnectorCertificateError(
            _connection_key(), ssl.SSLCertVerificationError("hostname mismatch")
        ),  # host 2 answers as somebody else
        _response(200, {"version": "9.2"}),  # host 3 answers
        _response(200, {"ok": 1}),  # the request, repeated on host 3
    ]

    assert await pve.request("GET", "nodes") == {"ok": 1}
    assert pve.host == "192.0.2.3"
