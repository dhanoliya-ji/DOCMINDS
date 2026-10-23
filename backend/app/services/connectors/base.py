"""
connectors/base.py
==================
WHAT THIS FILE DOES
-------------------
The shape every cloud provider is written to, so the importer, the sync and
the API never need to know which provider they are talking to:

    Provider.authorize_url(state, redirect_uri)   where to send the browser
    Provider.exchange_code(code, redirect_uri)    the OAuth code -> tokens
    Provider.refresh(tokens)                      a new access token
    Provider.list_items(tokens, parent, query)    the file / page picker
    Provider.get_item(tokens, item_id)            one item's CURRENT revision
    Provider.fetch(tokens, item, dest_dir)        the bytes, in a format the
                                                  extractor reads

Plus the plain data it passes around: TokenSet, RemoteItem, FetchedFile.

ITEM IDS ARE UNTRUSTED
----------------------
An id arrives from the browser (the picker sends back what it was shown).
It is only ever placed in a URL PATH, percent-encoded, and must first pass
`check_id` -- so "../../admin" or "x?y=z" can never change which endpoint of
the provider we call, let alone which host.
"""

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
    # Unix time the access token stops working; None = it does not expire.
    expires_at: Optional[float] = None
    # Provider extras that must survive a refresh (Confluence's cloud id).
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
        """A standard OAuth token response. Keeps the old refresh token and
        extras when the provider does not send new ones (Google does not)."""
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
    # "folder": can be opened in the picker, not imported.
    # "file" / "page": can be imported (when `importable`).
    kind: str
    mime_type: Optional[str] = None
    modified_at: Optional[str] = None
    # The provider's change marker; a different value means "changed".
    revision: Optional[str] = None
    size: Optional[int] = None
    web_url: Optional[str] = None
    importable: bool = True
    # Pages that also contain pages (Notion, Confluence) can be opened too.
    has_children: bool = False
    # The extension the imported document will have (".docx" for a Google
    # Doc exported to Word); None for a file kept in its own format.
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
    # Hosts API calls may go to; ".example.com" allows its subdomains.
    allowed_hosts: Tuple[str, ...] = ()
    # Extra hosts a DOWNLOAD may be redirected to (pre-signed file URLs).
    redirect_hosts: Tuple[str, ...] = ()

    def configured(self) -> bool:  # pragma: no cover - overridden
        return False

    def authorize_url(self, state: str, redirect_uri: str) -> str:  # pragma: no cover
        raise NotImplementedError

    async def exchange_code(self, code: str, redirect_uri: str) -> Tuple[TokenSet, str]:  # pragma: no cover
        raise NotImplementedError

    async def refresh(self, tokens: TokenSet) -> TokenSet:
        raise ConnectorAuthError("This connection has expired. Reconnect it.")

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

    # ---- helpers shared by the subclasses ----

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
