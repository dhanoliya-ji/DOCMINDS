"""Notion through its public REST API (version 2022-06-28)."""
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.services.connectors.base import FetchedFile, Provider, RemoteItem, TokenSet, path_id
from app.services.connectors.http import ConnectorAuthError, ConnectorError, SourceNotFound, TooLarge, request

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MAX_BLOCKS = 5000
MAX_DEPTH = 5


def rich_text(parts: List[Dict[str, Any]]) -> str:
    """Notion rich text -> plain text, links kept in Markdown form."""
    out = []
    for part in parts or []:
        text = part.get("plain_text", "")
        href = part.get("href")
        out.append(f"[{text}]({href})" if href and text else text)
    return "".join(out)


def page_title(page: Dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            title = rich_text(prop.get("title") or [])
            if title:
                return title
    return "Untitled"


def block_to_markdown(block: Dict[str, Any], depth: int = 0) -> List[str]:
    """One block -> its Markdown line(s). Children are rendered by the caller."""
    kind = block.get("type", "")
    body = block.get(kind) or {}
    text = rich_text(body.get("rich_text") or [])
    indent = "  " * depth
    if kind in ("heading_1", "heading_2", "heading_3"):
        return [f"{'#' * (int(kind[-1]) + 1)} {text}", ""]
    if kind == "paragraph":
        return [f"{indent}{text}", ""] if text else [""]
    if kind == "bulleted_list_item":
        return [f"{indent}- {text}"]
    if kind == "numbered_list_item":
        return [f"{indent}1. {text}"]
    if kind == "to_do":
        return [f"{indent}- [{'x' if body.get('checked') else ' '}] {text}"]
    if kind == "toggle":
        return [f"{indent}- {text}"]
    if kind in ("quote", "callout"):
        return [f"> {text}", ""]
    if kind == "code":
        return [f"```{body.get('language') or ''}", text, "```", ""]
    if kind == "equation":
        return [f"$${body.get('expression', '')}$$", ""]
    if kind == "divider":
        return ["---", ""]
    if kind == "child_page":
        return [f"## {body.get('title') or 'Untitled'} (sub-page)", ""]
    if kind == "table_row":
        cells = [rich_text(cell) for cell in body.get("cells") or []]
        return ["| " + " | ".join(cells) + " |"]
    if kind in ("bookmark", "embed", "link_preview") and body.get("url"):
        return [f"<{body['url']}>", ""]
    return []


class Notion(Provider):
    id = "notion"
    name = "Notion"
    allowed_hosts = ("api.notion.com",)
    manage_access_url = "https://www.notion.so/profile/integrations"

    def configured(self) -> bool:
        return bool(settings.CONNECTOR_NOTION_CLIENT_ID and settings.CONNECTOR_NOTION_CLIENT_SECRET)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"{API}/oauth/authorize?" + urlencode(
            {
                "client_id": settings.CONNECTOR_NOTION_CLIENT_ID,
                "response_type": "code",
                "owner": "user",
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )

    def _basic(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(settings.CONNECTOR_NOTION_CLIENT_ID, settings.CONNECTOR_NOTION_CLIENT_SECRET)

    def _headers(self, tokens: TokenSet) -> Dict[str, str]:
        return self._bearer(tokens, **{"Notion-Version": NOTION_VERSION})

    async def exchange_code(self, code: str, redirect_uri: str) -> Tuple[TokenSet, str]:
        body = await self._token_request(
            f"{API}/oauth/token",
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            what="Connecting Notion", auth=self._basic(), as_json=True,
        )
        tokens = TokenSet.from_oauth(body)
        tokens.extra["workspace_id"] = body.get("workspace_id")
        return tokens, body.get("workspace_name") or "Notion"

    async def refresh(self, tokens: TokenSet) -> TokenSet:
        if not tokens.refresh_token:
            raise ConnectorAuthError("Notion no longer accepts this connection. Reconnect it.")
        body = await self._token_request(
            f"{API}/oauth/token",
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
            what="Refreshing the Notion connection", auth=self._basic(), as_json=True,
        )
        return TokenSet.from_oauth(body, previous=tokens)

    async def revoke(self, tokens: TokenSet) -> bool:
        """Notion's token revocation endpoint (POST /v1/oauth/revoke, client"""
        if not tokens.access_token:
            return True
        try:
            await request(
                "POST", f"{API}/oauth/revoke", allowed=self.allowed_hosts, what="Revoking Notion access",
                json={"token": tokens.access_token}, auth=self._basic(),
                headers={"Notion-Version": NOTION_VERSION},
            )
        except ConnectorError as exc:
            if exc.status_code != 400:
                raise
        return True

    def _page(self, raw: Dict[str, Any]) -> RemoteItem:
        return RemoteItem(
            id=raw["id"],
            name=page_title(raw),
            kind="page",
            modified_at=raw.get("last_edited_time"),
            revision=raw.get("last_edited_time"),
            web_url=raw.get("url"),
            has_children=True,
            export_extension=".md",
        )

    async def list_items(self, tokens, parent=None, query=None, cursor=None) -> Tuple[List[RemoteItem], Optional[str]]:
        if parent and not query:
            params: Dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            body = (
                await request(
                    "GET", f"{API}/blocks/{path_id(parent)}/children", allowed=self.allowed_hosts,
                    what="Listing Notion sub-pages", headers=self._headers(tokens), params=params,
                )
            ).json()
            items = [
                RemoteItem(
                    id=block["id"], name=(block.get("child_page") or {}).get("title") or "Untitled",
                    kind="page", modified_at=block.get("last_edited_time"),
                    revision=block.get("last_edited_time"), has_children=True, export_extension=".md",
                )
                for block in body.get("results", [])
                if block.get("type") == "child_page"
            ]
            return items, body.get("next_cursor") if body.get("has_more") else None

        payload: Dict[str, Any] = {
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            "page_size": 50,
        }
        if query:
            payload["query"] = query
        if cursor:
            payload["start_cursor"] = cursor
        body = (
            await request(
                "POST", f"{API}/search", allowed=self.allowed_hosts, what="Searching Notion",
                headers=self._headers(tokens), json=payload,
            )
        ).json()
        items = [self._page(raw) for raw in body.get("results", []) if raw.get("object") == "page"]
        return items, body.get("next_cursor") if body.get("has_more") else None

    async def get_item(self, tokens, item_id: str) -> RemoteItem:
        raw = (
            await request(
                "GET", f"{API}/pages/{path_id(item_id)}", allowed=self.allowed_hosts,
                what="Reading the Notion page", headers=self._headers(tokens),
            )
        ).json()
        if raw.get("archived") or raw.get("in_trash"):
            raise SourceNotFound("The Notion page was moved to the trash.", 404)
        return self._page(raw)

    async def _blocks(self, tokens, block_id: str, depth: int, budget: List[int], lines: List[str]) -> None:
        cursor: Optional[str] = None
        while True:
            params: Dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            body = (
                await request(
                    "GET", f"{API}/blocks/{path_id(block_id)}/children", allowed=self.allowed_hosts,
                    what="Reading the Notion page", headers=self._headers(tokens), params=params,
                )
            ).json()
            previous_was_row = False
            for block in body.get("results", []):
                budget[0] -= 1
                if budget[0] < 0:
                    return
                rendered = block_to_markdown(block, depth if block.get("type", "").endswith("list_item") else 0)
                if block.get("type") == "table_row":
                    lines.extend(rendered)
                    if not previous_was_row:
                        cells = len((block.get("table_row") or {}).get("cells") or [])
                        lines.append("|" + " --- |" * max(cells, 1))
                    previous_was_row = True
                    continue
                previous_was_row = False
                lines.extend(rendered)
                if block.get("has_children") and block.get("type") != "child_page" and depth < MAX_DEPTH:
                    await self._blocks(tokens, block["id"], depth + 1, budget, lines)
                    if block.get("type") == "table":
                        lines.append("")
            if not body.get("has_more") or not body.get("next_cursor"):
                return
            cursor = body["next_cursor"]

    async def fetch(self, tokens, item: RemoteItem, dest_path: str) -> FetchedFile:
        lines: List[str] = [f"# {item.name}", ""]
        await self._blocks(tokens, item.id, 0, [MAX_BLOCKS], lines)
        data = "\n".join(lines).strip().encode("utf-8") + b"\n"
        if len(data) > settings.MAX_UPLOAD_SIZE_BYTES:
            raise TooLarge("The Notion page is larger than the upload limit.")
        with open(dest_path, "wb") as out:
            out.write(data)
        base = os.path.splitext(item.name)[0] if item.name.lower().endswith(".md") else item.name
        return FetchedFile(path=dest_path, filename=f"{base}.md")
