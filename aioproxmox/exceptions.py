"""Exceptions for aioproxmox."""


class ProxmoxError(Exception):
    """Base exception for all aioproxmox client errors."""


class ProxmoxAuthError(ProxmoxError):
    """Authentication or token verification failed."""


class ProxmoxAPIError(ProxmoxError):
    """PVE API returned an error status or a response that could not be read."""

    def __init__(self, status: int, message: str, endpoint: str) -> None:
        """Format error string."""
        super().__init__(f"PVE API Error {status} at {endpoint}: {message}")
        self.status = status
        self.endpoint = endpoint


class ProxmoxNodeUnreachableError(ProxmoxAPIError):
    """The node answering could not reach the node the request was for.

    pveproxy answers 595 when it cannot connect to the other cluster node and
    596 when that connection times out or its TLS fails.
    """


class ProxmoxResponseError(ProxmoxAPIError):
    """PVE API answered, but not with the JSON it should have."""


class ResourceNotFoundError(ProxmoxError):
    """Target VMID, LXC, Node, or Storage could not be found."""


class ProxmoxConnectionError(ProxmoxError):
    """The PVE API could not be reached or the connection failed."""


class ProxmoxSSLError(ProxmoxConnectionError):
    """The TLS handshake or certificate verification failed."""


class ProxmoxTimeoutError(ProxmoxConnectionError):
    """The request to the PVE API timed out."""
