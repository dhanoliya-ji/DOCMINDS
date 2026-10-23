"""
connectors/google_drive.py
==========================
Google Drive through the Drive v3 REST API.

    OAuth     accounts.google.com, scope drive.readonly, offline access (so a
              refresh token is issued and auto-sync keeps working)
    picker    files.list on a folder ("root" = My Drive), or a name search;
              shared drives included
    import    ordinary files are downloaded as they are (alt=media);
              Google Docs / Sheets / Slides are EXPORTED to .docx / .xlsx /
              .pptx, which the extractor reads. Other Google types (forms,
              drawings, sites) cannot be exported usefully and are shown as
              not importable.
    revision  Drive's `version`, which increases on every change, metadata
              included -- a cheap, reliable "has it changed?".
"""

import os
from typing import List, Optional, Tuple
from urllib.parse import urlencode

from app.core.config import settings
from app.services.connectors.base import FetchedFile, Provider, RemoteItem, TokenSet, check_id, path_id
from app.services.connectors.http import ConnectorError, download, request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# https://developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"

FOLDER = "application/vnd.google-apps.folder"
# Google-native type -> (export MIME type, extension).
EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}
FIELDS = "id,name,mimeType,modifiedTime,size,version,webViewLink"


def _quote_query(text: str) -> str:
    """Escape a value for Drive's query language ('...' strings)."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDrive(Provider):
    id = "google_drive"
    name = "Google Drive"
    allowed_hosts = ("accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com")
    manage_access_url = "https://myaccount.google.com/connections"

    def configured(self) -> bool:
        return bool(settings.CONNECTOR_GOOGLE_CLIENT_ID and settings.CONNECTOR_GOOGLE_CLIENT_SECRET)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return AUTH_URL + "?" + urlencode(
            {
                "client_id": settings.CONNECTOR_GOOGLE_CLIENT_ID,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE + " openid email",
                "access_type": "offline",
                # Always show consent, or a second connect returns no refresh token.
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
            }
        )

    def _client(self):
        return {
            "client_id": settings.CONNECTOR_GOOGLE_CLIENT_ID,
            "client_secret": settings.CONNECTOR_GOOGLE_CLIENT_SECRET,
        }

    async def exchange_code(self, code: str, redirect_uri: str) -> Tuple[TokenSet, str]:
        body = await self._token_request(
            TOKEN_URL,
            {**self._client(), "code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
            what="Connecting Google Drive",
        )
        tokens = TokenSet.from_oauth(body)
        about = await request(
            "GET", f"{API}/about", allowed=self.allowed_hosts, what="Reading the Google account",
            headers=self._bearer(tokens), params={"fields": "user(emailAddress,displayName)"},
        )
        user = about.json().get("user") or {}
        return tokens, user.get("emailAddress") or user.get("displayName") or "Google Drive"

    async def refresh(self, tokens: TokenSet) -> TokenSet:
        body = await self._token_request(
            TOKEN_URL,
            {**self._client(), "refresh_token": tokens.refresh_token or "", "grant_type": "refresh_token"},
            what="Refreshing the Google Drive connection",
        )
        return TokenSet.from_oauth(body, previous=tokens)

    async def revoke(self, tokens: TokenSet) -> bool:
        """Google's revocation endpoint. Revoking the refresh token ends the
        whole grant (its access tokens too). A 400 means the token is already
        invalid -- revoked or expired -- which is the outcome we want."""
        token = tokens.refresh_token or tokens.access_token
        if not token:
            return True
        try:
            await request("POST", REVOKE_URL, allowed=self.allowed_hosts, what="Revoking Google Drive access",
                          data={"token": token})
        except ConnectorError as exc:
            if exc.status_code != 400:
                raise
        return True

    def _item(self, raw: dict) -> RemoteItem:
        mime = raw.get("mimeType") or ""
        folder = mime == FOLDER
        export = EXPORTS.get(mime)
        native_unsupported = mime.startswith("application/vnd.google-apps.") and not export and not folder
        return RemoteItem(
            id=raw["id"],
            name=raw.get("name") or raw["id"],
            kind="folder" if folder else "file",
            mime_type=mime,
            modified_at=raw.get("modifiedTime"),
            revision=str(raw.get("version") or raw.get("modifiedTime") or ""),
            size=int(raw["size"]) if raw.get("size") else None,
            web_url=raw.get("webViewLink"),
            importable=not folder and not native_unsupported,
            export_extension=export[1] if export else None,
        )

    async def list_items(self, tokens, parent=None, query=None, cursor=None) -> Tuple[List[RemoteItem], Optional[str]]:
        clauses = ["trashed = false"]
        if query:
            clauses.append(f"name contains '{_quote_query(query)}'")
        else:
            clauses.append(f"'{_quote_query(check_id(parent) if parent else 'root')}' in parents")
        params = {
            "q": " and ".join(clauses),
            "fields": f"nextPageToken,files({FIELDS})",
            "pageSize": 50,
            "orderBy": "folder,name",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if cursor:
            params["pageToken"] = cursor
        response = await request(
            "GET", f"{API}/files", allowed=self.allowed_hosts, what="Listing Google Drive",
            headers=self._bearer(tokens), params=params,
        )
        body = response.json()
        return [self._item(raw) for raw in body.get("files", [])], body.get("nextPageToken")

    async def get_item(self, tokens, item_id: str) -> RemoteItem:
        response = await request(
            "GET", f"{API}/files/{path_id(item_id)}", allowed=self.allowed_hosts,
            what="Reading the Google Drive file", headers=self._bearer(tokens),
            params={"fields": FIELDS, "supportsAllDrives": "true"},
        )
        return self._item(response.json())

    async def fetch(self, tokens, item: RemoteItem, dest_path: str) -> FetchedFile:
        export = EXPORTS.get(item.mime_type or "")
        if export:
            url = f"{API}/files/{path_id(item.id)}/export"
            params = {"mimeType": export[0]}
            base = os.path.splitext(item.name)[0] if item.name.lower().endswith(export[1]) else item.name
            filename = base + export[1]
        else:
            url = f"{API}/files/{path_id(item.id)}"
            params = {"alt": "media", "supportsAllDrives": "true"}
            filename = item.name
        await download(
            url, dest_path, allowed=self.allowed_hosts, headers=self._auth_only(tokens),
            params=params, what=f"Downloading '{item.name}'",
        )
        return FetchedFile(path=dest_path, filename=filename)
