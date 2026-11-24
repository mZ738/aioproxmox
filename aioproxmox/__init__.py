"""Proxmox Home Assistant Integration Service."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
import logging
import ssl
import time
from typing import Any

import aiohttp

from .const import DEFAULT_PVE_PORT, PROBE_TIMEOUT
from .endpoints import AccessEndpoint, ClusterEndpoint, NodeEndpoint
from .exceptions import (
    ProxmoxAPIError,
    ProxmoxAuthError,
    ProxmoxConnectionError,
    ProxmoxError,
    ProxmoxNodeUnreachableError,
    ProxmoxResponseError,
    ProxmoxSSLError,
    ProxmoxTimeoutError,
)
from .model import PVECapabilities, PVEPermissions
from .model.pve import ClusterCache, ClusterResourcesCollection

_LOGGER = logging.getLogger(__name__)

NODE_UNREACHABLE_STATUSES = frozenset({595, 596})

SERVICES: dict[str, dict[str, Any]] = {
    "PVE": {"default_port": DEFAULT_PVE_PORT, "token_separator": "="}
    # Future PDM, PBS
}


@contextmanager
def _translate_client_errors(url: str) -> Iterator[None]:
    """Raise transport failures as aioproxmox errors instead of aiohttp ones."""
    try:
        yield
    except aiohttp.ClientSSLError as err:
        raise ProxmoxSSLError(f"SSL error connecting to {url}: {err}") from err
    except TimeoutError as err:
        raise ProxmoxTimeoutError(f"Timeout connecting to {url}") from err
    except aiohttp.ClientError as err:
        raise ProxmoxConnectionError(f"Error connecting to {url}: {err}") from err


async def _read_json(response: aiohttp.ClientResponse, endpoint: str) -> Any:
    """Decode a JSON body, raising a response that is not JSON as an API error."""
    try:
        return await response.json()
    except aiohttp.ContentTypeError as err:
        raise ProxmoxResponseError(response.status, err.message, endpoint) from err
    except ValueError as err:
        # JSONDecodeError, or UnicodeDecodeError for a body that isn't text
        raise ProxmoxResponseError(
            response.status, f"Invalid JSON: {err}", endpoint
        ) from err


def _normalize_host(host: str) -> str:
    """Strip a trailing port from the host and bracket IPv6 addresses for URLs."""
    if host.startswith("["):
        # "[2001:db8::1]" or "[2001:db8::1]:8006"
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    if host.count(":") == 1:
        # "pve.local:8006" or "192.0.2.1:8006"
        return host.split(":", 1)[0]
    if ":" in host:
        # Bare IPv6 address such as "2001:db8::1" or "::1"
        return f"[{host}]"
    return host


def _repeatable(method: str, err: ProxmoxConnectionError) -> bool:
    """Whether a request that failed may be sent again on another node.

    A read always may. Anything else only where the connection was never
    made: one that broke or timed out afterwards may have delivered the
    command, and a second `shutdown` or `vzdump` is worse than an error.
    """
    return method.upper() == "GET" or isinstance(
        err.__cause__, aiohttp.ClientConnectorError
    )


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
        `ssl.SSLContext` verifies against exactly what that context trusts.
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
        with _translate_client_errors(self.base_url):
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
            if response.status == 401:
                raise ProxmoxAuthError(
                    f"Couldn't authenticate user {self.username} to {self.base_url}/access/ticket: Code {response.status}"
                )
            if response.status != 200:
                raise ProxmoxAPIError(
                    response.status, await response.text(), "access/ticket"
                )
            res_json = await _read_json(response, "access/ticket")
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
                    if otpresp.status == 401:
                        raise ProxmoxAuthError(
                            f"Couldn't authenticate user {self.username} to {self.base_url}/access/ticket: Code {otpresp.status}"
                        )
                    if otpresp.status != 200:
                        raise ProxmoxAPIError(
                            otpresp.status, await otpresp.text(), "access/ticket"
                        )
                    otp_json = await _read_json(otpresp, "access/ticket")
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
        into the context and the login, every request and the probe of
        another node verify against it. See docs/certificates.md.
        """
        host = _normalize_host(host)

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
        self.cluster_resources = ClusterResourcesCollection()
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
        """Authenticate and gather cluster resources.

        Where other hosts are known already - `learn_hosts()` with a list
        kept from an earlier session - a configured host that does not
        answer the login is left for one of them, the way `request()`
        leaves it.
        """
        if hasattr(self.auth, "async_init"):
            try:
                await self.auth.async_init()
            except ProxmoxConnectionError as err:
                # The login goes to the host itself, so there is nothing
                # to ask it again.
                if not await self._moved_on(err, verify_current=False):
                    raise
                await self.auth.async_init()
        return await self.cluster.resources()

    def learn_hosts(self, hosts: list[str], *, replace: bool = False) -> None:
        """Remember other nodes of the cluster as places to fall back to.

        `cluster/status` says what address every node answers on. That is
        the corosync address, which on a cluster with a separate cluster
        network is not reachable from outside - so these are tried, not
        relied on. The configured host stays first and is used again after
        a reconnect.

        `replace` is for a caller holding a fresh membership list: a cluster
        that was reduced, or split into standalone servers, otherwise leaves
        its former peers in the list for good - and an address that once
        belonged to a peer can by then answer for a machine of its own. The
        configured host is kept, and so is the host in use, even when the
        cluster does not name it. A host that was known and is named again
        keeps its place.
        """
        current = self.host
        named: list[str] = []
        for host in hosts:
            if not isinstance(host, str) or not host:
                continue
            # Spelled the way the configured host is, or a bare IPv6
            # address and its bracketed self count as two.
            host = _normalize_host(host)
            if host not in named:
                named.append(host)
        # The cluster names its nodes in a different order every time it is
        # asked, and the order here is the order they are tried in.
        kept = [
            host
            for index, host in enumerate(self._hosts)
            if index == 0 or not replace or host in named
        ]
        kept += [host for host in named if host not in kept]
        if current not in kept:
            kept.append(current)
        self._hosts, self._host_index = kept, kept.index(current)

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

    def _base_url_of(self, host: str) -> str:
        return f"https://{_normalize_host(host)}:{self._port}/api2/json"

    def _use_host(self, index: int) -> None:
        self._host_index = index
        self.base_url = self._base_url_of(self._hosts[index])
        self.auth.base_url = self.base_url

    async def _answers(self, host: str) -> bool:
        """Whether `host` answers `version` within the probe timeout.

        `version` is the one read every credential may make, and it is
        answered by the host itself rather than forwarded to another node.
        The host is asked by its own address and the client left where it
        is, so the requests running meanwhile are not sent to a host
        nobody has heard from yet.

        Any answer counts, a refusal included: a host that says 401 is a
        host that is there. Before a password login there is no ticket to
        send, so 401 is exactly what a healthy host replies - counting that
        as silence left a client that had lost its host unable to log in
        anywhere else.
        """
        try:
            await self._request_once(
                "GET",
                "version",
                # Never longer than a request itself waits.
                timeout=min(PROBE_TIMEOUT, self.timeout),
                base_url=self._base_url_of(host),
            )
        except ProxmoxAPIError as err:
            _LOGGER.debug("Host %s answered %s; it is there", host, err.status)
        except ProxmoxAuthError:
            # 401 comes as its own error since the 401 split; still an answer.
            _LOGGER.debug("Host %s answered 401; it is there", host)
        except ProxmoxError as err:
            _LOGGER.debug("Host %s did not answer: %s", host, err)
            return False
        return True

    async def _moved_on(
        self,
        err: ProxmoxConnectionError,
        *,
        from_host: str | None = None,
        verify_current: bool = True,
    ) -> bool:
        """Whether a connection error was answered by moving to another node.

        A certificate that does not verify is not a host that is gone, and
        the node next to it is no answer to it: that one is reported.
        """
        if isinstance(err, ProxmoxSSLError) or len(self._hosts) < 2:
            return False
        return await self.failover(from_host=from_host, verify_current=verify_current)

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
        counts, and is asked by its own address: the client moves once,
        to the first that answered, and not at all where none did.
        Returns whether this call moved to a working host.

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
            if from_host is not None and from_host != self.host:
                _LOGGER.debug(
                    "Another request already moved to %s; repeating this one there",
                    self.host,
                )
                return True
            left = self.host
            if verify_current and await self._answers(left):
                _LOGGER.debug(
                    "%s still answers; leaving the request to fail on its own",
                    left,
                )
                return False
            start = self._host_index
            for host in self._hosts[start + 1 :] + self._hosts[:start]:
                # The list may have been replaced while the host was asked.
                if not await self._answers(host) or host not in self._hosts:
                    continue
                self._use_host(self._hosts.index(host))
                _LOGGER.warning(
                    "Proxmox at %s stopped answering; using %s until it is back",
                    left,
                    host,
                )
                return True
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

        Repeated there is a read, always. A command is repeated only where
        the connection was never made; one that broke after it may have
        reached Proxmox, so the client moves and the error is raised.

        The two can meet in one request - the ticket is refused on the node
        the client has just moved to, or the login finds the host gone -
        and each is answered once, in whichever order they come.
        """
        host_before = self.host
        try:
            with _translate_client_errors(self.base_url):
                await self.auth.check_and_refresh(method=method)
        except ProxmoxConnectionError as err:
            # The renewal goes to the host itself, so its failure is proof
            # enough and there is nothing to ask the host again.
            if not await self._moved_on(
                err, from_host=host_before, verify_current=False
            ):
                raise
            with _translate_client_errors(self.base_url):
                await self.auth.check_and_refresh(method=method)
        # One login and one move at most: every turn uses up one of them.
        moved = logged_in = False
        while True:
            host_before = self.host
            try:
                return await self._request_once(method, path, json_data, params)
            except ProxmoxAuthError:
                relogin = getattr(self.auth, "relogin", None)
                if relogin is None or logged_in:
                    raise
                _LOGGER.debug("Request to %s refused with 401, logging in again", path)
                logged_in = True
                if await self._logged_in_again(
                    relogin, from_host=host_before, may_move=not moved
                ):
                    moved = True
            except ProxmoxConnectionError as err:
                if moved or not await self._moved_on(err, from_host=host_before):
                    raise
                moved = True
                if not _repeatable(method, err):
                    # The client has moved, but Proxmox may hold this
                    # command already.
                    raise

    async def _logged_in_again(
        self,
        relogin: Callable[[], Awaitable[None]],
        *,
        from_host: str,
        may_move: bool,
    ) -> bool:
        """Log in with the stored password; return whether that took a move.

        The login goes to the host itself, like the renewal: where it finds
        nobody there is nothing to ask the host again, and the login is
        made on the next node instead. What it follows was refused, not
        run, so the request goes there whatever it is.
        """
        try:
            with _translate_client_errors(self.base_url):
                await relogin()
        except ProxmoxConnectionError as err:
            if not may_move or not await self._moved_on(
                err, from_host=from_host, verify_current=False
            ):
                raise
            with _translate_client_errors(self.base_url):
                await relogin()
            return True
        return False

    async def _request_once(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        *,
        base_url: str | None = None,
    ) -> Any:
        """One attempt in `timeout` at most, against the current host.

        `base_url` is for asking another host without moving to it.
        """

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

        url = f"{(base_url or self.base_url).rstrip('/')}/{path.lstrip('/')}"
        client_timeout = aiohttp.ClientTimeout(total=timeout or self.timeout)

        with _translate_client_errors(url):
            async with self.auth.session.request(
                method=method,
                url=url,
                headers=headers,
                timeout=client_timeout,
                ssl=self.verify_ssl,
                **request_kwargs,
            ) as response:
                if response.status == 401:
                    # A rejected API token or an expired ticket - the
                    # credentials are the problem, not the request
                    raise ProxmoxAuthError(
                        f"Couldn't authenticate to {url}: Code {response.status}"
                    )
                if response.status not in (200, 201):
                    # Proxmox puts what went wrong into the HTTP reason phrase -
                    # "Permission check failed (/nodes/pve, Sys.PowerMgmt)" - and
                    # answers with a body of `{"data":null}`. Keeping the body
                    # alone left the caller with nothing to report, so keep the
                    # reason, and the body where it says more.
                    text = (await response.text()).strip()
                    reason = response.reason if isinstance(response.reason, str) else ""
                    message = text
                    if not text or text.replace(" ", "") == '{"data":null}':
                        message = reason
                    elif reason and reason not in text:
                        message = f"{reason}: {text}"
                    if response.status in NODE_UNREACHABLE_STATUSES:
                        raise ProxmoxNodeUnreachableError(
                            response.status, message, path
                        )
                    raise ProxmoxAPIError(response.status, message, path)

                payload = await _read_json(response, path)
                # A read answers with an object or a list; a command answers
                # with the task id it queued, which is a string. Turning
                # anything else into `{}` dropped exactly that - every
                # `start`, `stop` or `vzdump` came back as if it had said
                # nothing. Hand back what Proxmox said.
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
