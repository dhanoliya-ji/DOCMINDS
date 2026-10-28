"""The shape every cloud provider is written to, so the importer, the sync and the API never need to know..."""
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from app.services.connectors.http import ConnectorAuthError, ConnectorError, request

_SAFE_ID = re.compile(r"^[A-Za-z0-9!._:~,\-]{1,400}$")


def check_id(item_id: Optional[str]) -> str:
    """Return `item_id` if it is a plausible provider id; raise otherwise."""
    if not item_id or not _SAFE_ID.match(item_id) or ".." in item_id:
        raise ConnectorError("That is not a valid file or page id.", 400)
    return item_id


def path_id(item_id: str) -> str:
    """An id percent-encoded for use as ONE path segment."""
    return quote(check_id(item_id), safe="")


@dataclass
class TokenSet:
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def needs_refresh(self, margin: float = 60.0) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at - margin

    def to_dict(self) -> Dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenSet":
        return cls(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            extra=dict(data.get("extra") or {}),
        )

    @classmethod
    def from_oauth(cls, body: Dict[str, Any], previous: Optional["TokenSet"] = None) -> "TokenSet":
        """A standard OAuth token response. Keeps the old refresh token and"""
        if not body.get("access_token"):
            raise ConnectorError("The provider did not return an access token.")
        expires_in = body.get("expires_in")
        return cls(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token") or (previous.refresh_token if previous else None),
            expires_at=time.time() + float(expires_in) if expires_in else None,
            extra=dict(previous.extra) if previous else {},
        )


@dataclass
class RemoteItem:
    id: str
    name: str
    kind: str
    mime_type: Optional[str] = None
    modified_at: Optional[str] = None
    revision: Optional[str] = None
    size: Optional[int] = None
    web_url: Optional[str] = None
    importable: bool = True
    has_children: bool = False
    export_extension: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "mime_type": self.mime_type,
            "modified_at": self.modified_at,
            "revision": self.revision,
            "size": self.size,
            "web_url": self.web_url,
            "importable": self.importable,
            "has_children": self.has_children,
        }


@dataclass
class FetchedFile:
    path: str      # a temporary file on this machine
    filename: str  # the display name, extension included


class Provider:
    """Base class. Subclasses fill in the class attributes and methods."""

    id: str = ""
    name: str = ""
    allowed_hosts: Tuple[str, ...] = ()
    redirect_hosts: Tuple[str, ...] = ()
    manage_access_url: Optional[str] = None

    def configured(self) -> bool:  # pragma: no cover - overridden
        return False

    def authorize_url(self, state: str, redirect_uri: str) -> str:  # pragma: no cover
        raise NotImplementedError

    async def exchange_code(self, code: str, redirect_uri: str) -> Tuple[TokenSet, str]:  # pragma: no cover
        raise NotImplementedError

    async def refresh(self, tokens: TokenSet) -> TokenSet:
        raise ConnectorAuthError("This connection has expired. Reconnect it.")

    async def revoke(self, tokens: TokenSet) -> bool:
        """Revoke DocMinds' grant at the provider. True when revoked; False when"""
        return False

    async def list_items(
        self,
        tokens: TokenSet,
        parent: Optional[str] = None,
        query: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> Tuple[List[RemoteItem], Optional[str]]:  # pragma: no cover
        raise NotImplementedError

    async def get_item(self, tokens: TokenSet, item_id: str) -> RemoteItem:  # pragma: no cover
        raise NotImplementedError

    async def fetch(self, tokens: TokenSet, item: RemoteItem, dest_path: str) -> FetchedFile:  # pragma: no cover
        raise NotImplementedError


    async def _token_request(self, url: str, data: Dict[str, Any], *, what: str, auth=None, as_json: bool = False):
        response = await request(
            "POST",
            url,
            allowed=self.allowed_hosts,
            what=what,
            data=None if as_json else data,
            json=data if as_json else None,
            auth=auth,
            headers={"Accept": "application/json"},
        )
        return response.json()

    def _bearer(self, tokens: TokenSet, **extra: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {tokens.access_token}", "Accept": "application/json", **extra}

    def _auth_only(self, tokens: TokenSet) -> Dict[str, str]:
        """For downloads: no Accept header, which some exports would refuse."""
        return {"Authorization": f"Bearer {tokens.access_token}"}
