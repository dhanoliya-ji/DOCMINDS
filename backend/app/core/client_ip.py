"""
client_ip.py
============
WHAT THIS FILE DOES
-------------------
Answers one question, in one place: "which IP address is this request from?"

Three things need the answer -- the per-IP rate limits on sign-in
(core/rate_limit.py), the audit log (services/audit.py) and the access log
(core/middleware.py) -- and they must all agree, or an attacker throttled by
one address would show up in the audit trail under another.

WHY IT IS NOT JUST `request.client.host`
----------------------------------------
That is the address of whatever opened the TCP connection to us. With nothing
in front of the API, that IS the user. But on Render, Heroku, Fly, behind
nginx or a cloud load balancer, every request arrives FROM THE PROXY -- so
every user would appear to share one address, and one person tripping the
sign-in limit would lock everybody out.

The proxy passes the real address along in a header:

    X-Forwarded-For: <client>, <proxy 1>, <proxy 2>

Each proxy APPENDS the address it received the request from. The catch is
that the client can send this header too, with anything in it:

    X-Forwarded-For: 1.2.3.4                          <- typed by an attacker
    X-Forwarded-For: 1.2.3.4, <attacker's real IP>    <- after our proxy appends

The leftmost entry is therefore worthless -- it is whatever the client
claimed. Only the entries appended by proxies WE control can be believed,
and those are at the RIGHT-hand end. With TRUSTED_PROXY_COUNT=1, the last
entry is the address our one proxy saw: the real client.

So the rules, in order:
    1. CLIENT_IP_HEADER set and present (e.g. CF-Connecting-IP, which
       Cloudflare overwrites) -> that.
    2. TRUSTED_PROXY_COUNT = N > 0 and X-Forwarded-For has at least N
       entries -> the Nth from the right.
    3. Otherwise the TCP peer -- the safe default when nothing is trusted.
Anything that does not parse as an IP address is ignored, so garbage in a
header can never become a rate-limit key or an audit entry.
"""

import ipaddress
from typing import Optional

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import Scope

from app.core.config import settings

# The longest textual IPv6 address is 45 characters; the audit column is sized
# to match (see models/audit.py).
_MAX_LENGTH = 45


def _valid_ip(candidate: str) -> Optional[str]:
    """`candidate` as a normalised IP address, or None if it is not one."""
    candidate = candidate.strip().strip('"')
    if candidate.startswith("[") and "]" in candidate:
        # "[2001:db8::1]:443" -- an IPv6 address with a port.
        candidate = candidate[1 : candidate.index("]")]
    elif candidate.count(":") == 1:
        # "203.0.113.7:51234" -- an IPv4 address with a port.
        candidate = candidate.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def client_ip_from_scope(scope: Scope) -> Optional[str]:
    """The client's address for a raw ASGI scope (what middleware sees)."""
    headers = Headers(scope=scope)

    if settings.CLIENT_IP_HEADER:
        value = headers.get(settings.CLIENT_IP_HEADER)
        if value:
            address = _valid_ip(value.split(",")[0])
            if address:
                return address

    if settings.TRUSTED_PROXY_COUNT > 0:
        # Several X-Forwarded-For headers are one list, in order (RFC 9110).
        hops = [
            hop.strip()
            for value in headers.getlist("x-forwarded-for")
            for hop in value.split(",")
            if hop.strip()
        ]
        if len(hops) >= settings.TRUSTED_PROXY_COUNT:
            address = _valid_ip(hops[-settings.TRUSTED_PROXY_COUNT])
            if address:
                return address
        # Fewer entries than proxies: this request did not come through all
        # of them, so the header proves nothing. Fall through to the peer.

    client = scope.get("client")
    if client and client[0]:
        return str(client[0])[:_MAX_LENGTH]
    return None


def client_ip(request: Optional[Request]) -> Optional[str]:
    """The client's address for a FastAPI/Starlette request, or None."""
    if request is None:
        return None
    return client_ip_from_scope(request.scope)
