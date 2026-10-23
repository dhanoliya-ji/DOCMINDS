"""
connectors/onedrive.py
======================
Microsoft OneDrive and SharePoint through Microsoft Graph.

    OAuth     login.microsoftonline.com/<CONNECTOR_MICROSOFT_TENANT>, scopes
              Files.Read.All + Sites.Read.All + offline_access
    picker    the signed-in user's OneDrive, plus every SharePoint site they
              FOLLOW, shown as folders at the top level; a name search
              covers their OneDrive
    import    files are downloaded in their own format -- Word, Excel and
              PowerPoint files already are .docx / .xlsx / .pptx
    revision  Graph's cTag, which changes only when the CONTENT changes (a
              rename does not trigger a re-index); eTag when there is none

ITEM IDS
--------
A Graph item is addressed by (drive id, item id), so the id the picker sees
is "<drive id>~<item id>"; a SharePoint site is "site~<site id>". Both parts
pass base.check_id and are percent-encoded into the path.

DOWNLOADS REDIRECT
------------------
/content answers 302 with a short-lived, pre-signed URL on SharePoint or the
OneDrive CDN. http.download follows it only to the hosts in
`redirect_hosts`, without our token.
"""

from typing import List, Optional, Tuple
from urllib.parse import quote, urlencode

from app.core.config import settings
from app.services.connectors.base import FetchedFile, Provider, RemoteItem, TokenSet, check_id, path_id
from app.services.connectors.http import ConnectorError, SourceNotFound, download, request

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Files.Read.All Sites.Read.All"
SELECT = "id,name,size,file,folder,package,lastModifiedDateTime,eTag,cTag,webUrl,parentReference"


def _login(path: str) -> str:
    tenant = quote(settings.CONNECTOR_MICROSOFT_TENANT or "common", safe="")
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/{path}"


def split_id(item_id: str) -> Tuple[str, str]:
    """'<drive>~<item>' -> (drive, item)."""
    check_id(item_id)
    drive, sep, item = item_id.partition("~")
    if not sep or not drive or not item:
        raise ConnectorError("That is not a valid OneDrive item id.", 400)
    return drive, item


class OneDrive(Provider):
    id = "onedrive"
    name = "OneDrive & SharePoint"
    allowed_hosts = ("login.microsoftonline.com", "graph.microsoft.com")
    redirect_hosts = (".sharepoint.com", ".1drv.com", ".onedrive.com", ".livefilestore.com", ".svc.ms")

    def configured(self) -> bool:
        return bool(settings.CONNECTOR_MICROSOFT_CLIENT_ID and settings.CONNECTOR_MICROSOFT_CLIENT_SECRET)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return _login("authorize") + "?" + urlencode(
            {
                "client_id": settings.CONNECTOR_MICROSOFT_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "response_mode": "query",
                "scope": SCOPES,
                "state": state,
            }
        )

    def _client(self):
        return {
            "client_id": settings.CONNECTOR_MICROSOFT_CLIENT_ID,
            "client_secret": settings.CONNECTOR_MICROSOFT_CLIENT_SECRET,
            "scope": SCOPES,
        }

    async def exchange_code(self, code: str, redirect_uri: str) -> Tuple[TokenSet, str]:
        body = await self._token_request(
            _login("token"),
            {**self._client(), "code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
            what="Connecting OneDrive",
        )
        tokens = TokenSet.from_oauth(body)
        me = (
            await request("GET", f"{GRAPH}/me", allowed=self.allowed_hosts, what="Reading the Microsoft account",
                          headers=self._bearer(tokens))
        ).json()
        return tokens, me.get("userPrincipalName") or me.get("mail") or me.get("displayName") or "OneDrive"

    async def refresh(self, tokens: TokenSet) -> TokenSet:
        body = await self._token_request(
            _login("token"),
            {**self._client(), "refresh_token": tokens.refresh_token or "", "grant_type": "refresh_token"},
            what="Refreshing the OneDrive connection",
        )
        return TokenSet.from_oauth(body, previous=tokens)

    def _item(self, raw: dict) -> RemoteItem:
        drive = (raw.get("parentReference") or {}).get("driveId") or ""
        folder = "folder" in raw
        file_facet = raw.get("file")
        return RemoteItem(
            id=f"{drive}~{raw['id']}",
            name=raw.get("name") or raw["id"],
            kind="folder" if folder else "file",
            mime_type=(file_facet or {}).get("mimeType"),
            modified_at=raw.get("lastModifiedDateTime"),
            revision=raw.get("cTag") or raw.get("eTag"),
            size=raw.get("size"),
            web_url=raw.get("webUrl"),
            # OneNote notebooks and other "packages" have no single file.
            importable=bool(file_facet) and "package" not in raw,
        )

    async def _children(self, tokens, url: str, params=None):
        response = await request(
            "GET", url, allowed=self.allowed_hosts, what="Listing OneDrive",
            headers=self._bearer(tokens), params=params,
        )
        body = response.json()
        items = [self._item(raw) for raw in body.get("value", []) if raw.get("id")]
        return items, body.get("@odata.nextLink")

    async def list_items(self, tokens, parent=None, query=None, cursor=None) -> Tuple[List[RemoteItem], Optional[str]]:
        if cursor:
            # Graph's own next-page link. Only ever one of OUR Graph URLs.
            if not cursor.startswith(GRAPH + "/"):
                raise ConnectorError("Invalid page cursor.", 400)
            return await self._children(tokens, cursor)
        params = {"$top": 50, "$select": SELECT}
        if query:
            escaped = query.replace("'", "''")
            return await self._children(
                tokens, f"{GRAPH}/me/drive/root/search(q='{quote(escaped, safe='')}')", params
            )
        if parent and parent.startswith("site~"):
            site = path_id(parent.split("~", 1)[1])
            return await self._children(tokens, f"{GRAPH}/sites/{site}/drive/root/children", params)
        if parent:
            drive, item = split_id(parent)
            return await self._children(
                tokens, f"{GRAPH}/drives/{path_id(drive)}/items/{path_id(item)}/children", params
            )

        items, next_link = await self._children(tokens, f"{GRAPH}/me/drive/root/children", params)
        # SharePoint sites the user follows, as folders. Personal accounts
        # have none and may refuse the call: that is not an error here.
        try:
            sites = await request(
                "GET", f"{GRAPH}/me/followedSites", allowed=self.allowed_hosts,
                what="Listing SharePoint sites", headers=self._bearer(tokens),
                params={"$select": "id,displayName,webUrl"},
            )
            for site in sites.json().get("value", []):
                if site.get("id"):
                    items.insert(0, RemoteItem(
                        id=f"site~{site['id']}", name=f"SharePoint: {site.get('displayName') or site['id']}",
                        kind="folder", web_url=site.get("webUrl"), importable=False,
                    ))
        except ConnectorError:
            pass
        return items, next_link

    async def get_item(self, tokens, item_id: str) -> RemoteItem:
        drive, item = split_id(item_id)
        response = await request(
            "GET", f"{GRAPH}/drives/{path_id(drive)}/items/{path_id(item)}",
            allowed=self.allowed_hosts, what="Reading the OneDrive file",
            headers=self._bearer(tokens), params={"$select": SELECT},
        )
        raw = response.json()
        if raw.get("deleted"):
            raise SourceNotFound("The file was deleted.", 404)
        return self._item(raw)

    async def fetch(self, tokens, item: RemoteItem, dest_path: str) -> FetchedFile:
        drive, item_part = split_id(item.id)
        await download(
            f"{GRAPH}/drives/{path_id(drive)}/items/{path_id(item_part)}/content",
            dest_path, allowed=self.allowed_hosts, redirect_allowed=self.redirect_hosts,
            headers=self._auth_only(tokens), what=f"Downloading '{item.name}'",
        )
        return FetchedFile(path=dest_path, filename=item.name)
