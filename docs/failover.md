# Falling back to another node

Every request goes to one host, and that host's `pveproxy` answers for the
whole cluster: a path names a node, and what is not the host's own is
forwarded. So the host being down takes the cluster with it — which is why
this client keeps a list of the others and moves when it has to.

## The list

`learn_hosts_from_cluster()` reads `cluster/status` and remembers the
address every node joined the cluster on. `learn_hosts()` takes addresses
from anywhere else, for a consumer that has its own, better source — one it
stored from an earlier session, or one the user typed. The configured host
stays first in the list, whatever is added.

Addresses are kept the way the configured host is — without a port, an IPv6
address in brackets — so one host spelled two ways is one entry. A name and
the address behind it are still two: a host configured by name shows up
again by its address once the client has moved and another node lists it.
That costs one more probe in a round, and nothing else.

Adding is the default because a list that only grows cannot lose an address
that is still good. A caller with a fresh membership list can pass
`replace=True` instead: a cluster that was reduced, or split into standalone
servers, otherwise keeps its former peers for good, and an address that once
belonged to a peer can by then answer for a machine of its own. Replacing
keeps the configured host and the host in use — the latter even when the
cluster does not name it, since dropping the address you are talking to
would be absurd. It is for answers that name nodes; an empty or unreadable
one is no evidence that anything was removed, and that judgement belongs to
the caller.

Two limits are worth knowing, and neither is the client's to solve. The
addresses in `cluster/status` are corosync's; a cluster with a separate
cluster network answers there and nowhere Home Assistant can reach. And
with certificate verification on, a fallback node has to present a
certificate valid for that address, which per-node certificates are not.

## When it moves

`failover()` asks the current host `version` before it switches. That is
the one read every credential may make, and the host answers it itself
rather than forwarding it — so it separates the two things a failed request
can mean:

- the host is gone, and another node has to serve the request;
- the host is fine and the request is not: a guest or a storage on a node
  that is down, where `pveproxy` fails the connection or answers `595 No
  route to host` on that node's behalf.

Without that question the second case reads as the first, and the client
walks the cluster — each request switching one host further, repeating the
same doomed read, with nothing to fix at the end of it. On a cluster of two
the walk lands on the node that is down.

A request that failed on a host the client has already left is repeated
without any of that: `request()` remembers the host it was on and passes
it as `from_host`, and where that is no longer the host in use, another
request has moved on already. Otherwise a burst - and a consumer with
forty coordinators polls in bursts - would have every request either walk
one more node or fail for a cycle while the new host answers the probe.

A candidate has to answer `version` too before it counts, and any answer
counts - a refusal included. Before a password login there is no ticket
to send, so `401` is what a perfectly healthy host replies; taking that
for silence left a client that had lost its host unable to log in
anywhere else. Only a connection that fails, or one that never answers,
means the host is gone. A probe gets `PROBE_TIMEOUT` or the client's own
`timeout`, whichever is shorter: it runs after a request has already
waited that out, and with a long timeout a cluster of four would otherwise
spend minutes establishing what it could say in seconds.

A candidate is asked by its own address, and the client stays where it is
until one has answered. Only the requests that failed wait for that; the
others keep going to the host in use rather than to one nobody has heard
from yet. Where no candidate answers, the client has not moved at all.

The ticket renewal is the exception, and passes `verify_current=False`:
`access/ticket` is answered by the host itself, so a connection error there
is proof enough and there is nothing to ask again.

The first login is the same case: `connect()` leaves a configured host that
does not answer the login for the next one that does. It needs a list for
that, and before there is a session `cluster/status` cannot supply one - so
that is the consumer's part: `learn_hosts()` with what it kept from last
time, before `connect()`. A consumer that calls `auth.async_init()` itself
gets the bare login and does the same by hand: on a
`ProxmoxConnectionError`, `failover(verify_current=False)` and the login
again.

## What a consumer sees

`host` is the address in use, `hosts` the whole list in order. A switch is
logged as a warning naming both ends, every probe that came back empty at
debug level. Where no host answers, the original error is raised and the
consumer decides what that means.

`request()` repeats a read once on the new host. A command - anything that
is not a `GET` - is repeated only where the connection was never made: a
port that refused, an address with no route, a name that did not resolve. A
connection that broke or timed out after it was made may have delivered the
command, and a second `shutdown` or `vzdump` is worse than an error. So the
client still moves, the `ProxmoxConnectionError` is raised, and the next
call goes to the new host. A host that is switched off is met as a timeout,
which says nothing about how far the request got - a command sent to it is
raised, not repeated.

The client stays on the node it moved to until that one stops answering
too. It does not go back on its own when the configured host returns; a
new client starts there again.

What counts as "the host stopped" is a `ProxmoxConnectionError` - a
connection that failed or timed out. A certificate that does not verify
(`ProxmoxSSLError`) is left out on purpose: that host is there, and what is
wrong with it is for the user to hear about rather than for the next node
to cover up. Anything the host answered, a `401` (`ProxmoxAuthError`) or a
`595` (`ProxmoxNodeUnreachableError`, a forwarded node that is down)
included, is an answer and never moves the client.
