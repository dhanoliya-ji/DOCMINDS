"""
DocMinds Python SDK
===================
A small synchronous client for the DocMinds API, built on httpx.

    from docminds import DocMinds

    client = DocMinds("https://docminds-api.onrender.com", api_key="dm_...")
    for project in client.list_projects():
        print(project["name"])

Responses are the API's JSON as plain dicts, with TypedDicts describing the
common shapes (they mirror the OpenAPI schema in frontend/openapi.json).
Errors raise DocMindsError carrying the status, the API's `detail`, and on a
429 the `Retry-After` wait in seconds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

try:  # TypedDict with NotRequired needs 3.11; fall back to plain total=False.
    from typing import TypedDict
except ImportError:  # pragma: no cover
    from typing_extensions import TypedDict  # type: ignore

import httpx

__all__ = ["DocMinds", "DocMindsError", "Project", "Document", "SearchResult", "SearchResponse"]
__version__ = "0.1.0"


class Project(TypedDict, total=False):
    id: str
    name: str
    org_id: str
    created_at: str
    updated_at: str


class Document(TypedDict, total=False):
    id: str
    project_id: str
    filename: str
    file_type: str
    file_size: int
    status: str  # pending | processing | completed | failed
    error_message: Optional[str]
    meta_data: Dict[str, Any]


class SearchResult(TypedDict, total=False):
    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    content: str
    score: float
    page_number: Optional[int]
    match_type: Optional[str]


class SearchResponse(TypedDict, total=False):
    query: str
    results: List[SearchResult]
    count: int
    took_seconds: float


class DocMindsError(Exception):
    """A non-2xx answer from the API."""

    def __init__(self, status: int, detail: Any, retry_after: Optional[float] = None,
                 request_id: Optional[str] = None) -> None:
        self.status = status
        self.detail = detail
        self.retry_after = retry_after
        self.request_id = request_id
        super().__init__(f"{status}: {detail}")


FileLike = Union[str, Path]


class DocMinds:
    """Client for one DocMinds deployment. Use as a context manager, or call close()."""

    def __init__(self, base_url: str, api_key: Optional[str] = None, *,
                 access_token: Optional[str] = None, timeout: float = 60.0,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        headers: Dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        self._http = httpx.Client(base_url=base_url.rstrip("/") + "/api/v1", headers=headers,
                                  timeout=timeout, transport=transport)

    # ---- plumbing -------------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DocMinds":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, path, **kwargs)
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            retry = response.headers.get("Retry-After")
            raise DocMindsError(response.status_code, detail,
                                float(retry) if retry else None,
                                response.headers.get("X-Request-ID"))
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ---- endpoints ------------------------------------------------------
    def me(self) -> Dict[str, Any]:
        return self.request("GET", "/users/me")

    def list_projects(self) -> List[Project]:
        return self.request("GET", "/projects/")

    def create_project(self, name: str) -> Project:
        return self.request("POST", "/projects/", json={"name": name})

    def list_documents(self, project_id: str) -> List[Document]:
        return self.request("GET", "/documents/", params={"project_id": project_id})

    def get_document(self, document_id: str) -> Document:
        return self.request("GET", f"/documents/{document_id}")

    def upload(self, project_id: str, paths: Iterable[FileLike]) -> List[Document]:
        """Upload files from disk. They return `pending`; poll get_document for `completed`."""
        handles = [open(Path(p), "rb") for p in paths]
        try:
            files = [("files", (Path(h.name).name, h)) for h in handles]
            return self.request("POST", "/documents/upload", data={"project_id": project_id}, files=files)
        finally:
            for h in handles:
                h.close()

    def search(self, query: str, *, project_id: Optional[str] = None, top_k: Optional[int] = None,
               document_ids: Optional[Sequence[str]] = None) -> SearchResponse:
        body: Dict[str, Any] = {"query": query}
        if project_id:
            body["project_id"] = project_id
        if top_k:
            body["top_k"] = top_k
        if document_ids:
            body["document_ids"] = list(document_ids)
        return self.request("POST", "/search/", json=body)

    def create_session(self, project_id: str, title: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"project_id": project_id}
        if title:
            body["title"] = title
        return self.request("POST", "/chat/sessions", json=body)

    def ask(self, session_id: str, message: str, *, mode: Optional[str] = None,
            top_k: Optional[int] = None, document_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"message": message}
        if mode:
            body["mode"] = mode
        if top_k:
            body["top_k"] = top_k
        if document_ids:
            body["document_ids"] = list(document_ids)
        return self.request("POST", f"/chat/sessions/{session_id}/messages", json=body)
