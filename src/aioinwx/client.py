"""Asynchronous client for the INWX DomRobot JSON-RPC API."""

import asyncio
import binascii
from datetime import UTC, datetime
import time
from types import TracebackType
from typing import Any, Self

import aiohttp
import pyotp

from .exceptions import (
    InwxApiError,
    InwxAuthError,
    InwxConnectionError,
    InwxInvalidSharedSecretError,
    InwxTwoFactorError,
    InwxTwoFactorRequiredError,
)
from .models import InwxAccount, InwxBalance, InwxDomain, InwxPrice

API_URL = "https://api.domrobot.com/jsonrpc/"

CODE_AUTH_ERROR = 2200
DOMAIN_LIST_PAGE_LIMIT = 100
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
TOTP_INTERVAL = 30

# A TOTP code may only be used once. Two logins of the same user within one
# time step would reuse the code, so remember the last used time step per user.
_last_totp_step: dict[str, int] = {}


def normalize_shared_secret(shared_secret: str) -> str:
    """Return the shared secret without spaces and in upper case."""
    return "".join(shared_secret.split()).upper()


class InwxClient:
    """Client for the INWX DomRobot JSON-RPC API.

    The API session is kept in a cookie. The client sends this cookie itself, so a
    passed session should not store cookies (``aiohttp.DummyCookieJar``) and must not
    be shared with another client of a different account.
    """

    def __init__(
        self,
        username: str,
        password: str,
        shared_secret: str | None = None,
        *,
        session: aiohttp.ClientSession | None = None,
        url: str = API_URL,
    ) -> None:
        """Initialize the client."""
        self._username = username
        self._password = password
        self._shared_secret = normalize_shared_secret(shared_secret) if shared_secret else None
        self._session = session
        self._close_session = False
        self._url = url
        self._cookies: dict[str, str] = {}

    async def __aenter__(self) -> Self:
        """Enter the context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the session the client created itself."""
        await self.close()

    async def close(self) -> None:
        """Close the session the client created itself."""
        if self._session is not None and self._close_session:
            await self._session.close()
            self._session = None
            self._close_session = False

    def _get_session(self) -> aiohttp.ClientSession:
        """Return the session and create one if none was passed."""
        if self._session is None:
            self._session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
            self._close_session = True
        return self._session

    async def _call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call an API method and return its result data."""
        payload = {"method": method, "params": params or {}}
        try:
            async with self._get_session().post(
                self._url,
                json=payload,
                cookies=self._cookies,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                response.raise_for_status()
                body = await response.json(content_type=None)
                for name, morsel in response.cookies.items():
                    self._cookies[name] = morsel.value
        except (aiohttp.ClientError, TimeoutError) as err:
            raise InwxConnectionError(f"Request {method} failed: {err}") from err
        except ValueError as err:
            raise InwxApiError(None, f"Invalid response for {method}") from err

        if not isinstance(body, dict) or not isinstance(body.get("code"), int):
            raise InwxApiError(None, f"Invalid response for {method}")
        code: int = body["code"]
        if code >= 2000:
            message = str(body.get("msg", ""))
            if code == CODE_AUTH_ERROR:
                raise InwxAuthError(message)
            raise InwxApiError(code, message)
        res_data = body.get("resData")
        return res_data if isinstance(res_data, dict) else {}

    async def _async_totp_code(self, shared_secret: str) -> str:
        """Return a TOTP code that has not been used yet."""
        step = int(time.time()) // TOTP_INTERVAL
        if _last_totp_step.get(self._username, -1) >= step:
            await asyncio.sleep(TOTP_INTERVAL - time.time() % TOTP_INTERVAL + 1)
            step = int(time.time()) // TOTP_INTERVAL
        try:
            code: str = pyotp.TOTP(shared_secret, interval=TOTP_INTERVAL).at(
                datetime.fromtimestamp(step * TOTP_INTERVAL, UTC)
            )
        except (binascii.Error, ValueError) as err:
            raise InwxInvalidSharedSecretError("Shared secret is not valid Base32") from err
        _last_totp_step[self._username] = step
        return code

    async def login(self) -> InwxAccount:
        """Log in and unlock the session with 2FA if required."""
        self._cookies.clear()
        res_data = await self._call(
            "account.login", {"user": self._username, "pass": self._password}
        )
        if str(res_data.get("tfa", "0")) != "0":
            if not self._shared_secret:
                raise InwxTwoFactorRequiredError("Account requires a 2FA shared secret")
            tan = await self._async_totp_code(self._shared_secret)
            try:
                await self._call("account.unlock", {"tan": tan})
            except InwxApiError as err:
                raise InwxTwoFactorError(err.message) from err
            except InwxAuthError as err:
                raise InwxTwoFactorError(str(err)) from err
        try:
            return InwxAccount.from_api(res_data)
        except (KeyError, TypeError, ValueError) as err:
            raise InwxApiError(None, "Invalid login response") from err

    async def logout(self) -> None:
        """Log out and drop the session."""
        try:
            await self._call("account.logout")
        finally:
            self._cookies.clear()

    async def get_balance(self) -> InwxBalance:
        """Return the account balance."""
        res_data = await self._call("accounting.accountBalance")
        try:
            return InwxBalance.from_api(res_data)
        except (KeyError, TypeError, ValueError) as err:
            raise InwxApiError(None, "Invalid account balance") from err

    async def get_domains(self) -> list[InwxDomain]:
        """Return all domains of the account."""
        domains: list[InwxDomain] = []
        page = 1
        while True:
            res_data = await self._call(
                "domain.list", {"page": page, "pagelimit": DOMAIN_LIST_PAGE_LIMIT}
            )
            entries = res_data.get("domain") or []
            if not isinstance(entries, list):
                raise InwxApiError(None, "Invalid domain list")
            for entry in entries:
                try:
                    domains.append(InwxDomain.from_api(entry))
                except (KeyError, TypeError, ValueError) as err:
                    raise InwxApiError(None, f"Invalid domain entry: {err}") from err
            try:
                count = int(res_data["count"])
            except (KeyError, TypeError, ValueError) as err:
                raise InwxApiError(None, "Invalid domain list") from err
            if not entries or len(domains) >= count:
                return domains
            page += 1

    async def get_renewal_price(self, domain: InwxDomain) -> InwxPrice:
        """Return the renewal price of a domain for its period."""
        params: dict[str, Any] = {"domain": [domain.name_ace]}
        if domain.period:
            params["period"] = domain.period
        res_data = await self._call("domain.getalldomainprices", params)
        prices = res_data.get("domain")
        if not isinstance(prices, dict) or not isinstance(prices.get(domain.name_ace), dict):
            raise InwxApiError(None, f"No price for {domain.name}")
        try:
            return InwxPrice.from_api(prices[domain.name_ace])
        except (KeyError, TypeError, ValueError) as err:
            raise InwxApiError(None, f"Invalid price for {domain.name}") from err
