"""Read-only portal client: one sign-in form, then three GET pages."""

import asyncio
from urllib.parse import urljoin

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from yarl import URL

from . import portal

HOST = "myutilities.arlingtonva.us"
BASE = f"https://{HOST}"
MAX_BYTES = 2_000_000
TIMEOUT = 60
LOGIN = "/css/public/login"
UA = {"User-Agent": "Mozilla/5.0 (HomeAssistant)"}


class InvalidAuth(Exception):
    """Sign-in rejected."""


class CannotConnect(Exception):
    """Site down or behaving unexpectedly."""


class Client:
    """One short-lived session."""

    def __init__(self, hass: HomeAssistant, username: str, password: str) -> None:
        self._hass = hass
        self._username = username
        self._password = password
        self._session = async_create_clientsession(
            hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar()
        )

    def close(self) -> None:
        """Drop the session; the shared connector belongs to Home Assistant."""
        self._session.detach()

    async def _request(
        self, method: str, path: str, data: dict[str, str] | None = None
    ) -> tuple[str, str]:
        """Return (final path, body). Redirects never leave the host."""
        url = f"{BASE}{path}"
        for _ in range(6):
            async with self._session.request(
                method, url, data=data, headers=UA, allow_redirects=False
            ) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    target = URL(urljoin(url, resp.headers.get("Location", "")))
                    if target.scheme != "https" or target.host != HOST:
                        raise CannotConnect("unexpected redirect")
                    # The password is only ever sent once, to the sign-in form.
                    url, method, data = str(target), "GET", None
                    continue
                if resp.status != 200:
                    raise CannotConnect(f"HTTP {resp.status}")
                raw = bytearray()
                async for chunk in resp.content.iter_chunked(65536):
                    raw += chunk
                    if len(raw) > MAX_BYTES:
                        raise CannotConnect("page too large")
                return URL(url).path, raw.decode("utf-8", "replace")
        raise CannotConnect("too many redirects")

    async def _page(self, path: str):
        _, body = await self._request("GET", path)
        page = await self._hass.async_add_executor_job(portal.parse, body)
        if page.login_form:
            raise CannotConnect("session not accepted")
        return page

    async def login(self) -> None:
        """Sign in once. Never retried here."""
        try:
            async with asyncio.timeout(TIMEOUT):
                _, body = await self._request("GET", f"{LOGIN}/form")
                form = await self._hass.async_add_executor_job(portal.parse, body)
                if not form.login_form or not form.csrf:
                    raise CannotConnect("sign-in page changed")
                path, body = await self._request(
                    "POST",
                    LOGIN,
                    {
                        "_csrf": form.csrf,
                        "username": self._username,
                        "password": self._password,
                    },
                )
                page = await self._hass.async_add_executor_job(portal.parse, body)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(type(err).__name__) from err
        if page.login_form or path.startswith(LOGIN):
            raise InvalidAuth

    async def fetch(self) -> tuple[portal.Summary, list[portal.Bill]]:
        """Sign in and read the account."""
        await self.login()
        try:
            async with asyncio.timeout(TIMEOUT):
                account = await self._page("/css/account/getAccount")
                transactions = await self._page("/css/account/accountTransaction")
                reads = await self._page(portal.consumption_path(account))
                return portal.summary(account), portal.bills(transactions, reads)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(type(err).__name__) from err
