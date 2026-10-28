"""Answers one question, in one place: "which IP address is this request from? """
import ipaddress
from typing import Optional

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import Scope

from app.core.config import settings

_MAX_LENGTH = 45


def _valid_ip(candidate: str) -> Optional[str]:
    """`candidate` as a normalised IP address, or None if it is not one."""
    candidate = candidate.strip().strip('"')
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    elif candidate.count(":") == 1:
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

    client = scope.get("client")
    if client and client[0]:
        return str(client[0])[:_MAX_LENGTH]
    return None


def client_ip(request: Optional[Request]) -> Optional[str]:
    """The client's address for a FastAPI/Starlette request, or None."""
    if request is None:
        return None
    return client_ip_from_scope(request.scope)
