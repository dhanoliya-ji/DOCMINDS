"""Confluence Cloud through Atlassian's OAuth 2.0 (3LO) and the REST API."""
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from app.core.config import settings
from app.services.connectors.base import FetchedFile, Provider, RemoteItem, TokenSet, check_id, path_id
from app.services.connectors.http import ConnectorError, SourceNotFound, TooLarge, request

AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
SCOPES = (
    "read:confluence-content.all read:confluence-space.summary "
    "read:confluence-content.summary search:confluence offline_access"
)
PAGE_SIZE = 50


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Confluence(Provider):
    id = "confluence"
    name = "Confluence"
    allowed_hosts = ("auth.atlassian.com", "api.atlassian.com")
    manage_access_url = "https://id.atlassian.com/manage-profile/apps"

    def configured(self) -> bool:
        return bool(settings.CONNECTOR_CONFLUENCE_CLIENT_ID and settings.CONNECTOR_CONFLUENCE_CLIENT_SECRET)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return AUTH_URL + "?" + urlencode(
            {
                "audience": "api.atlassian.com",
                "client_id": settings.CONNECTOR_CONFLUENCE_CLIENT_ID,
                "scope": SCOPES,
                "redirect_uri": redirect_uri,
                "state": state,
                "response_type": "code",
                "prompt": "consent",
            }
        )

    def _client(self):
        return {
            "client_id": settings.CONNECTOR_CONFLUENCE_CLIENT_ID,
            "client_secret": settings.CONNECTOR_CONFLUENCE_CLIENT_SECRET,
        }

    def _base(self, tokens: TokenSet) -> str:
        cloud_id = tokens.extra.get("cloud_id") or ""
        return f"https://api.atlassian.com/ex/confluence/{path_id(cloud_id)}/wiki/rest/api"

    async def exchange_code(self, code: str, redirect_uri: str) -> Tuple[TokenSet, str]:
        body = await self._token_request(
            TOKEN_URL,
            {**self._client(), "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            what="Connecting Confluence", as_json=True,
        )
        tokens = TokenSet.from_oauth(body)
        resources = (
            await request("GET", RESOURCES_URL, allowed=self.allowed_hosts, what="Finding the Confluence site",
                          headers=self._bearer(tokens))
        ).json()
        site = next(
            (r for r in resources if any("confluence" in scope for scope in r.get("scopes", []))),
            resources[0] if resources else None,
        )
        if not site or not site.get("id"):
            raise ConnectorError("This Atlassian account has no Confluence site DocMinds can read.")
        check_id(site["id"])
        tokens.extra.update({"cloud_id": site["id"], "site_url": site.get("url")})
        return tokens, site.get("name") or site.get("url") or "Confluence"

    async def refresh(self, tokens: TokenSet) -> TokenSet:
        body = await self._token_request(
            TOKEN_URL,
            {**self._client(), "grant_type": "refresh_token", "refresh_token": tokens.refresh_token or ""},
            what="Refreshing the Confluence connection", as_json=True,
        )
        return TokenSet.from_oauth(body, previous=tokens)

    def _page(self, raw: Dict[str, Any], tokens: TokenSet) -> RemoteItem:
        links = raw.get("_links") or {}
        base = links.get("base") or tokens.extra.get("site_url") or ""
        version = (raw.get("version") or {}).get("number")
        return RemoteItem(
            id=str(raw["id"]),
            name=raw.get("title") or str(raw["id"]),
            kind="page",
            modified_at=(raw.get("version") or {}).get("when"),
            revision=str(version) if version is not None else None,
            web_url=(base + links["webui"]) if links.get("webui") and base.startswith("https://") else None,
            has_children=True,
            export_extension=".html",
        )

    async def _get(self, tokens, path: str, params: Dict[str, Any], what: str) -> Dict[str, Any]:
        return (
            await request(
                "GET", self._base(tokens) + path, allowed=self.allowed_hosts, what=what,
                headers=self._bearer(tokens), params=params,
            )
        ).json()

    @staticmethod
    def _next(body: Dict[str, Any], start: int) -> Optional[str]:
        results = body.get("results", [])
        if (body.get("_links") or {}).get("next") or len(results) >= PAGE_SIZE:
            return str(start + len(results))
        return None

    async def list_items(self, tokens, parent=None, query=None, cursor=None) -> Tuple[List[RemoteItem], Optional[str]]:
        start = int(cursor) if cursor and cursor.isdigit() else 0
        params: Dict[str, Any] = {"limit": PAGE_SIZE, "start": start, "expand": "version"}
        if query:
            cleaned = query.replace("\\", " ").replace('"', " ").strip()
            params["cql"] = f'type=page AND title ~ "{cleaned}"'
            body = await self._get(tokens, "/content/search", params, "Searching Confluence")
            return [self._page(raw, tokens) for raw in body.get("results", [])], self._next(body, start)
        if parent and parent.startswith("space~"):
            key = check_id(parent.split("~", 1)[1])
            body = await self._get(
                tokens, f"/space/{quote(key, safe='')}/content/page", {**params, "depth": "root"},
                "Listing the Confluence space",
            )
        elif parent:
            body = await self._get(tokens, f"/content/{path_id(parent)}/child/page", params, "Listing child pages")
        else:
            body = await self._get(tokens, "/space", {"limit": PAGE_SIZE, "start": start}, "Listing Confluence spaces")
            spaces = [
                RemoteItem(id=f"space~{raw['key']}", name=raw.get("name") or raw["key"], kind="folder",
                           importable=False)
                for raw in body.get("results", []) if raw.get("key")
            ]
            return spaces, self._next(body, start)
        return [self._page(raw, tokens) for raw in body.get("results", [])], self._next(body, start)

    async def get_item(self, tokens, item_id: str) -> RemoteItem:
        raw = await self._get(tokens, f"/content/{path_id(item_id)}", {"expand": "version"}, "Reading the Confluence page")
        if raw.get("status") not in (None, "current"):
            raise SourceNotFound("The Confluence page was deleted or archived.", 404)
        return self._page(raw, tokens)

    async def fetch(self, tokens, item: RemoteItem, dest_path: str) -> FetchedFile:
        raw = await self._get(
            tokens, f"/content/{path_id(item.id)}", {"expand": "body.export_view,version"},
            "Exporting the Confluence page",
        )
        html = ((raw.get("body") or {}).get("export_view") or {}).get("value") or ""
        title = _escape_html(raw.get("title") or item.name)
        document = (
            f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head>'
            f"<body><h1>{title}</h1>{html}</body></html>"
        ).encode("utf-8")
        if len(document) > settings.MAX_UPLOAD_SIZE_BYTES:
            raise TooLarge("The Confluence page is larger than the upload limit.")
        with open(dest_path, "wb") as out:
            out.write(document)
        return FetchedFile(path=dest_path, filename=f"{item.name}.html")
