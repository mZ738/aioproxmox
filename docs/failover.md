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
means the host is gone. The probes get
`PROBE_TIMEOUT` rather than the client's own timeout: they run after a
request has already waited out `timeout`, and a cluster of four would
otherwise spend a minute establishing what it could say in ten seconds.

The ticket renewal is the exception, and passes `verify_current=False`:
`access/ticket` is answered by the host itself, so a connection error there
is proof enough and there is nothing to ask again.

## What a consumer sees

`host` is the address in use, `hosts` the whole list in order. A switch is
logged as a warning naming both ends, every probe that came back empty at
debug level. `request()` repeats the request once on the new host; where no
host answers, the original error is raised and the consumer decides what
that means.
