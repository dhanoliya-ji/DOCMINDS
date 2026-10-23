"""The Python SDK against a fake API (httpx.MockTransport): no server needed.

    cd sdk/python && python -m pytest
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docminds import DocMinds, DocMindsError  # noqa: E402


def make(handler):
    return DocMinds("https://api.example.com/", api_key="dm_test", transport=httpx.MockTransport(handler))


def test_sends_the_key_and_the_v1_prefix():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json=[{"id": "p1", "name": "HR"}])

    assert make(handler).list_projects() == [{"id": "p1", "name": "HR"}]
    assert seen == {"url": "https://api.example.com/api/v1/projects/", "key": "dm_test"}


def test_search_sends_only_the_options_given():
    def handler(request):
        assert json.loads(request.content) == {"query": "leave", "project_id": "p1", "top_k": 3}
        return httpx.Response(200, json={"query": "leave", "results": [], "count": 0, "took_seconds": 0.01})

    assert make(handler).search("leave", project_id="p1", top_k=3)["count"] == 0


def test_errors_carry_status_detail_and_retry_after():
    def handler(request):
        return httpx.Response(429, json={"detail": "Slow down"}, headers={"Retry-After": "12", "X-Request-ID": "r1"})

    with pytest.raises(DocMindsError) as info:
        make(handler).ask("s1", "hi")
    assert (info.value.status, info.value.detail, info.value.retry_after, info.value.request_id) == (
        429, "Slow down", 12.0, "r1")


def test_upload_is_multipart_with_the_project(tmp_path):
    path = tmp_path / "handbook.txt"
    path.write_text("Annual leave is 21 days.")

    def handler(request):
        body = request.content
        assert b'name="project_id"' in body and b"p1" in body
        assert b'filename="handbook.txt"' in body
        return httpx.Response(202, json=[{"id": "d1", "status": "pending"}])

    assert make(handler).upload("p1", [path])[0]["status"] == "pending"


def test_no_content_returns_none():
    assert make(lambda request: httpx.Response(204)).request("DELETE", "/projects/p1") is None
