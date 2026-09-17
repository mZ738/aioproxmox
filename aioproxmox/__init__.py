"""Proxmox Home Assistant Integration Service."""

import logging
import time
from typing import Any

import aiohttp

from .const import DEFAULT_PVE_PORT
from .endpoints import AccessEndpoint, ClusterEndpoint, NodeEndpoint
from .exceptions import ProxmoxAPIError, ProxmoxAuthError
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
        verify_ssl: bool = False,
    ):
        """Initialize ticket based authentication."""
        self.session = session
        self.timeout = timeout
        self.service = service
        self.verify_ssl = verify_ssl
        self.capabilities: PVECapabilities

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
        verify_ssl: bool = True,
        timeout: float = 5.0,
        token_name: str | None = None,
        token_value: str | None = None,
        service: str = "PVE",
    ) -> None:
        """HTTPS Backend for Proxmox Virtualisation Engine."""
        if ":" in host and not host.startswith("["):
            # Clean up base IPv4 parsing strings; ignore legacy bracket rules
            host, _ = host.split(":", 1)

        if not port:
            port = int(SERVICES[service]["default_port"])

        self.auth: ProxmoxHTTPAuthBase
        self.base_url = f"https://{host}:{port}/api2/json"
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

    async def request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[Any, Any] | list[Any] | dict[str, Any]:
        """Unified request pipeline managing tickets, CSRF tokens and cookies.

        A 401 with password authentication means the ticket died while the
        host was away: log in again and repeat once.
        """
        await self.auth.check_and_refresh(method=method)
        try:
            return await self._request_once(method, path, json_data, params)
        except ProxmoxAPIError as err:
            if err.status != 401 or not hasattr(self.auth, "relogin"):
                raise
            _LOGGER.debug("Request to %s refused with 401, logging in again", path)
            await self.auth.relogin()
            return await self._request_once(method, path, json_data, params)

    async def _request_once(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[Any, Any] | list[Any] | dict[str, Any]:
        """One attempt against the current host."""

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
        timeout = aiohttp.ClientTimeout(total=self.timeout)

        async with self.auth.session.request(
            method=method,
            url=url,
            headers=headers,
            timeout=timeout,
            ssl=self.verify_ssl,
            **request_kwargs,
        ) as response:
            if response.status not in (200, 201):
                text = await response.text()
                raise ProxmoxAPIError(response.status, text, path)

            payload = await response.json()
            data = payload.get("data", {})
            return data if isinstance(data, (dict, list)) else {}

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
