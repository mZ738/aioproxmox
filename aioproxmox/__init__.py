"""Proxmox Home Assistant Integration Service."""

import asyncio
import logging
import ssl
import time
from typing import Any

import aiohttp

from .const import DEFAULT_PVE_PORT, PROBE_TIMEOUT
from .endpoints import AccessEndpoint, ClusterEndpoint, NodeEndpoint
from .exceptions import ProxmoxAPIError, ProxmoxAuthError, ProxmoxError
from .model import PVECapabilities, PVEPermissions
from .model.pve import ClusterCache, ClusterResourcesCollection

_LOGGER = logging.getLogger(__name__)

SERVICES: dict[str, dict[str, Any]] = {
    "PVE": {"default_port": DEFAULT_PVE_PORT, "token_separator": "="}
    # Future PDM, PBS
}


class ProxmoxHTTPAuthBase:
    """Base class for authentication structures."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        timeout: float = 5.0,
        service: str = "PVE",
        verify_ssl: bool | ssl.SSLContext = False,
    ):
        """Initialize ticket based authentication.

        `verify_ssl` is handed to aiohttp as its `ssl` argument: True verifies
        against the session's trust store, False verifies nothing, and an
        `ssl.SSLContext` verifies against exactly what that context trusts -
        the way to accept a private cluster CA without giving up verification.
        """
        self.session = session
        self.timeout = timeout
        self.service = service
        self.verify_ssl = verify_ssl
        self.capabilities: PVECapabilities
        # Where the API answers; the client points it at another host on failover.
        self.base_url: str = ""

    def get_cookies(self) -> dict[str, str]:
        """Return cookies."""
        return {}

    def get_headers(self) -> dict[str, str]:
        """Return headers."""
        return {}

    async def check_and_refresh(self, method: str) -> None:
        """Asynchronously refresh credentials if required before a request."""


class ProxmoxHTTPAuth(ProxmoxHTTPAuthBase):
    """Ticket and Cookie based Authentication supporting TFA."""

    renew_age = 3600

    def __init__(
        self,
        username: str,
        password: str,
        otp: bool | None = None,
        base_url: str = "",
        otptype: str = "totp",
        **kwargs: Any,
    ) -> None:
        """Initialize ticket based authentication."""
        super().__init__(**kwargs)
        self.base_url = base_url
        self.username = username
        self.password = password
        self.otp = otp
        self.otptype = otptype
        self.pve_auth_ticket = ""
        self.csrf_prevention_token = ""
        self.birth_time = 0.0

    async def async_init(self) -> Any:
        """Initial token acquisition loop."""
        await self._get_new_tokens(
            password=self.password, otp=self.otp, otptype=self.otptype
        )
        return self

    async def _get_new_tokens(
        self,
        password: str | None = None,
        otp: bool | None = None,
        otptype: str | None = None,
    ) -> None:
        """Retrieve new tokens for session."""
        target_password = password or self.pve_auth_ticket
        data = {"username": self.username, "password": target_password}
        timeout = aiohttp.ClientTimeout(total=self.timeout)

        async with self.session.post(
            f"{self.base_url}/access/ticket",
            data=data,
            timeout=timeout,
            ssl=self.verify_ssl,
        ) as response:
            if response.status != 200:
                raise ProxmoxAuthError(
                    f"Couldn't authenticate user {self.username} to {self.base_url}/access/ticket: Code {response.status}"
                )
            res_json = await response.json()
            response_data = res_json["data"]

            self.birth_time = time.monotonic()
            self.pve_auth_ticket = response_data["ticket"]
            self.csrf_prevention_token = response_data["CSRFPreventionToken"]

            if "cap" in response_data:
                self.capabilities = PVECapabilities(**response_data["cap"])

            # Secondary step if Two Factor Challenge is detected
            if response_data.get("NeedTFA") is not None:
                otpdata = {
                    "username": self.username,
                    "tfa-challenge": self.pve_auth_ticket,
                    "password": f"{otptype}:{otp}",
                }
                async with self.session.post(
                    f"{self.base_url}/access/ticket",
                    data=otpdata,
                    timeout=timeout,
                    ssl=self.verify_ssl,
                ) as otpresp:
                    otp_json = await otpresp.json()
                    otpresp_data = otp_json.get("data")
                if not otpresp_data:
                    raise ProxmoxAuthError(
                        "Couldn't authenticate user: missing Two Factor Authentication (TFA)"
                    )

                self.birth_time = time.monotonic()
                self.pve_auth_ticket = otpresp_data["ticket"]
                self.csrf_prevention_token = otpresp_data["CSRFPreventionToken"]

    def get_cookies(self) -> dict[str, str]:
        """Return cookies."""
        return {f"{self.service}AuthCookie": self.pve_auth_ticket}

    def get_headers(self) -> dict[str, str]:
        """Return headers."""
        # Return CSRF prevention tokens strictly for mutation traffic
        return {"CSRFPreventionToken": self.csrf_prevention_token}

    async def check_and_refresh(self, method: str) -> None:
        """Asynchronously refresh credentials if required before a request.

        A ticket renews itself for as long as it is valid - two hours. After
        a host has been unreachable longer than that, the renewal is refused
        exactly like a wrong password would be; the password is still here,
        so log in again with it before giving up.
        """
        time_diff = time.monotonic() - self.birth_time
        if time_diff >= self.renew_age:
            _LOGGER.debug("Refreshing ticket (age %s)", time_diff)
            try:
                await self._get_new_tokens()
            except ProxmoxAuthError:
                _LOGGER.debug("Ticket renewal refused, logging in again")
                await self.relogin()

    async def relogin(self) -> None:
        """Log in again with the stored password, replacing ticket and CSRF token."""
        await self._get_new_tokens(
            password=self.password, otp=self.otp, otptype=self.otptype
        )


class ProxmoxHTTPApiTokenAuth(ProxmoxHTTPAuthBase):
    """Stateless API Token based Authentication."""

    def __init__(
        self,
        username: str,
        token_name: str,
        token_value: str,
        **kwargs: Any,
    ) -> None:
        """Initialize token based authentication."""
        super().__init__(**kwargs)
        self.username = username
        self.token_name = token_name
        self.token_value = token_value

    def get_headers(self) -> dict[str, str]:
        """Return headers."""
        sep = SERVICES[self.service]["token_separator"]
        auth_string = f"{self.service}APIToken={self.username}!{self.token_name}{sep}{self.token_value}"
        return {"Authorization": auth_string}


def _says_nothing(body: str) -> bool:
    """Whether an error body is Proxmox's empty `{"data":null}` or blank."""
    return body.replace(" ", "").replace("\n", "") in ("", '{"data":null}')


class ProxmoxVE:
    """Backend Engine coordinating configuration endpoints and session mapping."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        *,
        user: str | None = None,
        password: str | None = None,
        otp: str | None = None,
        port: int | None = None,
        verify_ssl: bool | ssl.SSLContext = True,
        timeout: float = 5.0,
        token_name: str | None = None,
        token_value: str | None = None,
        service: str = "PVE",
    ) -> None:
        """HTTPS Backend for Proxmox Virtualisation Engine.

        `verify_ssl` may be an `ssl.SSLContext` instead of a bool, for a
        Proxmox that presents a certificate from a private CA: load that CA
        into the context and every request verifies against it.
        """
        if ":" in host and not host.startswith("["):
            # Clean up base IPv4 parsing strings; ignore legacy bracket rules
            host, _ = host.split(":", 1)

        if not port:
            port = int(SERVICES[service]["default_port"])

        self.auth: ProxmoxHTTPAuthBase
        self.base_url = f"https://{host}:{port}/api2/json"
        # The configured host first, then whatever the cluster says its other
        # nodes answer on. Only the first is ever written anywhere.
        self._hosts: list[str] = [host]
        self._host_index = 0
        self._port = port
        self._switch_lock = asyncio.Lock()
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.permissions = PVEPermissions()
        self.cluster_resources: ClusterResourcesCollection
        self.cluster_cache = ClusterCache()

        auth_kwargs: dict[str, Any] = {
            "session": session,
            "verify_ssl": verify_ssl,
            "timeout": timeout,
            "service": service,
        }

        if token_name is not None:
            self.auth = ProxmoxHTTPApiTokenAuth(
                str(user), token_name, str(token_value), **auth_kwargs
            )
        elif password is not None:
            self.auth = ProxmoxHTTPAuth(
                str(user), password, bool(otp), base_url=self.base_url, **auth_kwargs
            )
        else:
            raise ProxmoxAuthError("No valid authentication credentials were supplied")

    async def connect(self) -> ClusterResourcesCollection:
        """Authenticate and gather cluster resources."""
        if hasattr(self.auth, "async_init"):
            await self.auth.async_init()
        return await self.cluster.resources()

    def learn_hosts(self, hosts: list[str]) -> None:
        """Remember other nodes of the cluster as places to fall back to.

        `cluster/status` says what address every node answers on. That is
        the corosync address, which on a cluster with a separate cluster
        network is not reachable from outside - so these are tried, not
        relied on. The configured host stays first and is used again after
        a reconnect.
        """
        for host in hosts:
            if isinstance(host, str) and host and host not in self._hosts:
                self._hosts.append(host)

    async def learn_hosts_from_cluster(self) -> None:
        """Ask the cluster where else the API answers and remember it."""
        try:
            entries = await self.cluster.status()
        except ProxmoxError as err:
            _LOGGER.debug("Could not read cluster/status to learn hosts: %s", err)
            return
        self.learn_hosts(
            [
                str(entry["ip"])
                for entry in entries
                if entry.get("type") == "node"
                and entry.get("ip")
                and not entry.get("local")
            ]
        )

    @property
    def host(self) -> str:
        """The host currently in use."""
        return self._hosts[self._host_index]

    @property
    def hosts(self) -> tuple[str, ...]:
        """Every host this client may use, the configured one first."""
        return tuple(self._hosts)

    def _use_host(self, index: int) -> None:
        self._host_index = index
        self.base_url = f"https://{self._hosts[index]}:{self._port}/api2/json"
        self.auth.base_url = self.base_url

    async def _answers(self) -> bool:
        """Whether the current host answers `version` within the probe timeout.

        `version` is the one read every credential may make, and it is
        answered by the host itself rather than forwarded to another node.

        Any answer counts, a refusal included: a host that says 401 is a
        host that is there. Before a password login there is no ticket to
        send, so 401 is exactly what a healthy host replies - counting that
        as silence left a client that had lost its host unable to log in
        anywhere else.
        """
        try:
            await self._request_once("GET", "version", timeout=PROBE_TIMEOUT)
        except ProxmoxAPIError as err:
            _LOGGER.debug("Host %s answered %s; it is there", self.host, err.status)
        except (ProxmoxError, aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Host %s did not answer: %s", self.host, err)
            return False
        return True

    async def failover(
        self, *, from_host: str | None = None, verify_current: bool = True
    ) -> bool:
        """Move to the next node that answers, once the current one stopped.

        The host is asked first whether it is really gone, because a failed
        request is not proof that it is: a path names a node, and the host
        forwards what is not its own - so a guest or a storage on a node
        that is down fails the connection while the host answering is
        perfectly well. Switching on that walks the whole cluster and, on a
        cluster of two, lands on the node that is actually down.

        Several requests may hit a dead host at once; the lock lets the
        first one switch, and the rest then find a host that answers and
        leave it alone. A candidate has to answer `version` before it
        counts. Returns whether this call moved to a working host.

        `from_host` is the host the caller was on when its request
        failed. Where that is no longer the host in use, another request
        has already moved the client and this one only has to be repeated
        - which is what keeps a burst of requests from each taking another
        step around the cluster.

        `verify_current` is for the callers that already have their proof:
        the ticket renewal is answered by the host itself and never
        forwarded, so a connection error there leaves nothing to check.
        """
        async with self._switch_lock:
            start = self._host_index
            if from_host is not None and from_host != self.host:
                _LOGGER.debug(
                    "Another request already moved to %s; repeating this one there",
                    self.host,
                )
                return True
            if verify_current and await self._answers():
                _LOGGER.debug(
                    "%s still answers; leaving the request to fail on its own",
                    self.host,
                )
                return False
            for offset in range(1, len(self._hosts)):
                index = (start + offset) % len(self._hosts)
                self._use_host(index)
                if not await self._answers():
                    continue
                _LOGGER.warning(
                    "Proxmox at %s stopped answering; using %s until it is back",
                    self._hosts[start],
                    self.host,
                )
                return True
            self._use_host(start)
            return False

    async def request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Unified request pipeline managing tickets, CSRF tokens and cookies.

        A 401 with password authentication means the ticket died while the
        host was away: log in again and repeat once. A host that does not
        answer at all is left for another node of the cluster when one is
        known, and the request repeated there - the ticket renewal that
        comes first included, since that is where a dead host is met once
        the ticket is an hour old.
        """
        try:
            await self.auth.check_and_refresh(method=method)
        except aiohttp.ClientConnectionError, TimeoutError:
            # The renewal goes to the host itself, so its failure is proof
            # enough and there is nothing to ask the host again.
            if len(self._hosts) < 2 or not await self.failover(verify_current=False):
                raise
            await self.auth.check_and_refresh(method=method)
        host_before = self.host
        try:
            return await self._request_once(method, path, json_data, params)
        except ProxmoxAPIError as err:
            if err.status != 401 or not hasattr(self.auth, "relogin"):
                raise
            _LOGGER.debug("Request to %s refused with 401, logging in again", path)
            await self.auth.relogin()
            return await self._request_once(method, path, json_data, params)
        except aiohttp.ClientConnectionError, TimeoutError:
            if len(self._hosts) < 2 or not await self.failover(from_host=host_before):
                raise
            return await self._request_once(method, path, json_data, params)

    async def _request_once(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """One attempt against the current host, in `timeout` at most."""

        headers: dict[str, str] = {
            "Accept": "application/json",
            "Connection": "keep-alive",
            **self.auth.get_headers(),
        }

        # Only attach the Cookie header if cookies are present (e.g., Ticket Auth)
        if cookies := self.auth.get_cookies():
            headers["Cookie"] = "; ".join([f"{k}={v}" for k, v in cookies.items()])

        request_kwargs: dict[str, Any] = {}

        if method.upper() in ("GET", "DELETE"):
            query_params = {**(params or {}), **(json_data or {})}
            if query_params:
                request_kwargs["params"] = {
                    k: (int(v) if isinstance(v, bool) else str(v))
                    for k, v in query_params.items()
                }
        elif json_data:
            request_kwargs["json"] = json_data

        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        client_timeout = aiohttp.ClientTimeout(total=timeout or self.timeout)

        async with self.auth.session.request(
            method=method,
            url=url,
            headers=headers,
            timeout=client_timeout,
            ssl=self.verify_ssl,
            **request_kwargs,
        ) as response:
            if response.status not in (200, 201):
                # Proxmox puts what went wrong into the HTTP reason phrase -
                # "Permission check failed (/nodes/pve, Sys.PowerMgmt)" - and
                # answers with a body of `{"data":null}`; keep the reason, and
                # the body only where it says more.
                text = await response.text()
                reason = response.reason if isinstance(response.reason, str) else ""
                message = reason if reason and _says_nothing(text) else text
                if reason and message is text and reason not in text:
                    message = f"{reason}: {text}"
                raise ProxmoxAPIError(response.status, message.strip(), path)

            payload = await response.json()
            # Reads answer with a dict or a list; a command answers with the
            # task id it queued - a string. Hand back what Proxmox said.
            return payload.get("data") if isinstance(payload, dict) else payload

    @property
    def cluster(self) -> ClusterEndpoint:
        """Add cluster endpoint."""
        return ClusterEndpoint(self)

    @property
    def access(self) -> AccessEndpoint:
        """Add access endpoint."""
        return AccessEndpoint(self)

    def nodes(self, node: str) -> NodeEndpoint:
        """Add nodes endpoint."""
        return NodeEndpoint(self, node)
