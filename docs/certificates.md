# Certificates

`verify_ssl` says what the client accepts from the host. It is handed to
aiohttp as it is - for the login, for every request, and for asking
another node whether it is there.

- `True` verifies against the session's trust store. It is the default.
- `False` verifies nothing.
- An `ssl.SSLContext` verifies against what that context trusts.

## A cluster that signs its own

A Proxmox cluster signs its nodes' certificates with a CA of its own,
`/etc/pve/pve-root-ca.pem`. No public trust store knows it, so with a bool
the choice is between a certificate that is always refused and no
verification at all. A context that trusts that CA keeps verification on:

```python
import ssl

context = ssl.create_default_context(cafile="pve-root-ca.pem")
context.verify_flags &= ~ssl.VERIFY_X509_STRICT

pve = aioproxmox.ProxmoxVE(
    session=session,
    host="pve.example.org",
    user="root@pam",
    password="your_secure_password",
    verify_ssl=context,
)
```

The second line is there for the CA Proxmox generates itself. It carries
no `keyUsage` extension, and the strict checks `ssl.create_default_context()`
turns on since Python 3.13 refuse a CA without one, however it was made
known. With the flag cleared the chain and the host name are still
verified.

Building a context reads files. In a running event loop that belongs in an
executor.

## When a certificate is refused

A certificate that does not verify raises `ProxmoxSSLError`. It never
moves the client to another node: that host is there, and what is wrong
with it is for the user to hear about. A node the client falls back to is
asked by the address the cluster names, so its certificate has to be valid
for that address too - see [Falling back to another node](failover.md).
