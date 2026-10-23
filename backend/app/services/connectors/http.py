"""
connectors/http.py
==================
WHAT THIS FILE DOES
-------------------
The ONLY way connector code talks to the network. Every request goes through
`request()` or `download()` here, which refuse anything but:

    * https://
    * a host on the provider's ALLOWLIST (www.googleapis.com,
      graph.microsoft.com, api.notion.com, api.atlassian.com ...)
    * a host whose every DNS address is PUBLIC -- the same check webhooks use
      (services/webhooks.py), so 169.254.169.254 and 10.0.0.0/8 stay out of
      reach even if a provider's DNS were poisoned

WHY THIS IS AN SSRF CONCERN AT ALL
----------------------------------
Connectors make OUR server fetch things. The URLs are built by our code from
fixed provider hosts, and the only user-supplied parts -- file and page ids --
are percent-encoded into the PATH, never the host. The one place a host comes
from outside is a REDIRECT: Microsoft Graph answers a download with a 302 to
a pre-signed SharePoint URL. Redirects are therefore never followed
automatically; `download()` follows at most three, each checked against the
redirect allowlist, and drops the Authorization header when the host changes
(the pre-signed URL carries its own permission, and our bearer token must not
leak to another host).

ERRORS
------
    ConnectorAuthError    401 -- the token is no longer valid (reconnect)
    SourceNotFound        403 / 404 -- gone, or we no longer have access
    ConnectorUnavailable  429 / 5xx / timeout / connection error -- try later
    ConnectorError        anything else, and every refusal of this module
"""

import asyncio
import contextlib
import ipaddress
import os
import socket
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlsplit

import httpx

from app.core.config import settings


class ConnectorError(Exception):
    """A connector failure whose message is safe to show the user."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ConnectorAuthError(ConnectorError):
    """The provider refused our token: the connection must be reconnected."""


class SourceNotFound(ConnectorError):
    """The file or page no longer exists, or we no longer have access to it."""


class ConnectorUnavailable(ConnectorError):
    """The provider is rate-limiting us or is down. Retrying later may work."""


class TooLarge(ConnectorError):
    """The file is bigger than MAX_UPLOAD_SIZE_BYTES."""


# Tests swap in an httpx.MockTransport here (and replace _resolves_publicly,
# since the fake hosts never reach DNS); None means the real network.
_transport: Optional[httpx.AsyncBaseTransport] = None

_MAX_REDIRECTS = 3


def _host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """Exact host, or a subdomain of an entry written with a leading dot."""
    host = host.lower().rstrip(".")
    for entry in allowed:
        entry = entry.lower()
        if entry.startswith("."):
            if host.endswith(entry) and len(host) > len(entry):
                return True
        elif host == entry:
            return True
    return False


def _resolves_publicly(host: str) -> bool:
    """True when EVERY address `host` resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return False
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if not ip.is_global or ip.is_multicast:
            return False
    return True


async def check_url(url: str, allowed: Iterable[str]) -> None:
    """Raise ConnectorError unless `url` is https, allowlisted and public."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ConnectorError("Refused a non-HTTPS address from the provider.")
    if parts.username or parts.password:
        raise ConnectorError("Refused an address carrying credentials.")
    if not _host_allowed(parts.hostname, allowed):
        raise ConnectorError(f"Refused to contact '{parts.hostname}': not a host this provider uses.")
    if not await asyncio.to_thread(_resolves_publicly, parts.hostname):
        raise ConnectorError(f"Refused to contact '{parts.hostname}': it resolves to a private address.")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.CONNECTOR_TIMEOUT_SECONDS,
        transport=_transport,
        follow_redirects=False,
    )


def raise_for_status(response: httpx.Response, what: str) -> None:
    """Map a provider's error status onto the exceptions above."""
    code = response.status_code
    if code < 400:
        return
    if code == 401:
        raise ConnectorAuthError(f"{what}: the provider no longer accepts this connection. Reconnect it.", code)
    if code in (403, 404, 410):
        raise SourceNotFound(f"{what}: not found, or access to it was removed.", code)
    if code == 429 or code >= 500:
        raise ConnectorUnavailable(f"{what}: the provider is busy or unavailable (HTTP {code}). Try again later.", code)
    raise ConnectorError(f"{what}: the provider answered HTTP {code}.", code)


async def request(
    method: str,
    url: str,
    *,
    allowed: Iterable[str],
    what: str = "Request",
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    json: Any = None,
    auth: Optional[httpx.Auth] = None,
) -> httpx.Response:
    """One checked request. Raises the errors above for any failure."""
    await check_url(url, allowed)
    try:
        async with _client() as client:
            response = await client.request(
                method, url, headers=headers, params=params, data=data, json=json, auth=auth
            )
    except httpx.TimeoutException as exc:
        raise ConnectorUnavailable(f"{what}: the provider did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise ConnectorUnavailable(f"{what}: could not reach the provider ({type(exc).__name__}).") from exc
    if response.is_redirect:
        raise ConnectorError(f"{what}: the provider answered with an unexpected redirect.")
    raise_for_status(response, what)
    return response


async def download(
    url: str,
    dest_path: str,
    *,
    allowed: Iterable[str],
    redirect_allowed: Iterable[str] = (),
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    max_bytes: Optional[int] = None,
    what: str = "Download",
) -> int:
    """
    Stream `url` into `dest_path`, following at most three checked
    redirects. Returns the number of bytes written; raises TooLarge the
    moment the file passes `max_bytes` (and removes the partial file).
    """
    limit = max_bytes if max_bytes is not None else settings.MAX_UPLOAD_SIZE_BYTES
    hosts = list(allowed)
    current_headers = dict(headers or {})
    current_params = params
    first_host = urlsplit(url).hostname
    try:
        async with _client() as client:
            for _hop in range(_MAX_REDIRECTS + 1):
                await check_url(url, hosts)
                async with client.stream("GET", url, headers=current_headers, params=current_params) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        url = str(response.url.join(location))
                        hosts = list(allowed) + list(redirect_allowed)
                        current_params = None
                        if urlsplit(url).hostname != first_host:
                            # A pre-signed URL on another host: our token stays home.
                            current_headers.pop("Authorization", None)
                        continue
                    raise_for_status(response, what)
                    size = 0
                    with open(dest_path, "wb") as out:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > limit:
                                raise TooLarge(
                                    f"{what}: the file is larger than the "
                                    f"{limit // (1024 * 1024)} MB upload limit."
                                )
                            out.write(chunk)
                    return size
            raise ConnectorError(f"{what}: too many redirects.")
    except TooLarge:
        with contextlib.suppress(OSError):
            os.remove(dest_path)
        raise
    except httpx.TimeoutException as exc:
        raise ConnectorUnavailable(f"{what}: the provider did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise ConnectorUnavailable(f"{what}: could not reach the provider ({type(exc).__name__}).") from exc
