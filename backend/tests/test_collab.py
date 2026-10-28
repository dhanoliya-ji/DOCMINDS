# ruff: noqa: F811  -- the harness fixtures are imported, then requested by name.
import datetime
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import ChatMessage, ChatSession
from app.models import ShareLink
from app.services import compare as compare_module
from app.services import confidence, sharing
from app.services.agent import run_agent
from app.services.llm import NOT_FOUND_ANSWER, LLMService

from tests.test_product_api import (  # noqa: F401
    API,
    _chunk_result,
    env,
    fast_password_hashing,
    no_rate_limits,
    queued,
    searches,
    store_dir,
)


def cite(number, score=0.8, document_id="doc-1"):
    return {"number": number, "score": score, "document_id": document_id, "filename": "a.pdf"}


class TestConfidence:
    def test_strong_and_well_cited_is_high(self):
        answer = "Refunds take thirty days [1]. Store credit is offered after that [2]."
        verdict = confidence.assess(answer, [cite(1, 0.82), cite(2, 0.7, "doc-2")], model="m")
        assert verdict["level"] == "high"
        codes = [r["code"] for r in verdict["reasons"]]
        assert "strong_match" in codes and "well_cited" in codes and "multiple_sources" in codes

    def test_weak_matches_and_no_markers_is_low(self):
        verdict = confidence.assess(
            "Refunds probably take a month or so in most cases.", [cite(1, 0.2)], model="m"
        )
        assert verdict["level"] == "low"
        assert {"weak_match", "few_citations"} <= {r["code"] for r in verdict["reasons"]}

    def test_invented_citation_numbers_are_penalised(self):
        good = confidence.assess("Refunds take thirty days here [1].", [cite(1, 0.7)], model="m")
        bad = confidence.assess("Refunds take thirty days here [1] [7].", [cite(1, 0.7)], model="m")
        assert bad["score"] < good["score"]
        assert any(r["code"] == "invented_citations" for r in bad["reasons"])

    def test_not_found_error_and_no_sources_are_low(self):
        assert confidence.assess(NOT_FOUND_ANSWER, [cite(1)], model="m")["level"] == "low"
        assert confidence.assess("x [1]", [cite(1)], model="error")["reasons"][0]["code"] == "model_error"
        assert confidence.assess("Some answer here.", [], model="m")["reasons"][0]["code"] == "no_sources"

    def test_demo_mode_is_never_high(self):
        verdict = confidence.assess("Refunds take thirty days [1].", [cite(1, 0.95)], model="mock")
        assert verdict["level"] != "high"
        assert any(r["code"] == "demo_mode" for r in verdict["reasons"])

    def test_tables_and_charts_are_not_uncited_claims(self):
        answer = (
            "Refund rates by quarter are shown below [1].\n\n"
            "| Quarter | Rate |\n|---|---|\n| Q1 | 12 |\n\n"
            '```chart\n{"type": "bar", "labels": ["Q1"], "series": [{"name": "r", "values": [12]}]}\n```'
        )
        verdict = confidence.assess(answer, [cite(1, 0.8)], model="m")
        assert verdict["coverage"] == 1.0

    def test_read_sections_without_scores_are_neutral(self):
        verdict = confidence.assess("The policy covers all staff [1].", [cite(1, 0.0)], model="m")
        assert verdict["retrieval"] == 0.5
        assert any(r["code"] == "read_sections" for r in verdict["reasons"])


def _streaming_model():
    """First response: some text, THEN a tool call (so the text was not the"""
    models_function = pytest.importorskip("pydantic_ai.models.function")
    messages_mod = pytest.importorskip("pydantic_ai.messages")

    def returned(messages):
        return sum(
            1 for m in messages for p in getattr(m, "parts", [])
            if isinstance(p, messages_mod.ToolReturnPart)
        )

    async def stream(messages, info):
        if returned(messages) == 0:
            yield "Let me look that up. "
            yield {0: models_function.DeltaToolCall(
                name="search_documents", json_args='{"query": "refunds"}', tool_call_id="c1")}
        else:
            for piece in ["Refunds take ", "thirty days ", "[1]."]:
                yield piece

    def respond(messages, info):  # pragma: no cover - only the stream is used
        raise AssertionError("expected a streaming request")

    return models_function.FunctionModel(respond, stream_function=stream)


class TestAgentStreaming:
    async def test_tokens_stream_with_a_reset_and_sources_first(self, monkeypatch):
        pytest.importorskip("pydantic_ai")
        from app.services.retrieval import RetrievalService

        found = {"chunk_id": uuid.uuid4(), "document_id": uuid.uuid4(), "filename": "p.pdf",
                 "page_number": 1, "chunk_index": 0, "content": "Thirty days.", "score": 0.9,
                 "meta_data": {}}

        async def search(**kwargs):
            return [found]

        monkeypatch.setattr(RetrievalService, "search", staticmethod(search))
        monkeypatch.setattr(settings, "AGENT_MAX_STEPS", 4)
        events = []
        result = await run_agent(
            db=None, question="Refunds?", org_id=uuid.uuid4(), project_id=uuid.uuid4(),
            model=_streaming_model(),
            on_step=lambda s: events.append(("step", s["tool"])),
            on_token=lambda t: events.append(("token", t)),
            on_reset=lambda: events.append(("reset", None)),
            on_citations=lambda c: events.append(("citations", len(c))),
        )
        kinds = [k for k, _ in events]
        assert "reset" in kinds
        after_reset = events[kinds.index("reset") + 1:]
        assert ("citations", 1) in after_reset, "sources are re-sent before the real answer"
        final = "".join(v for k, v in after_reset if k == "token")
        assert final == result["answer"] == "Refunds take thirty days [1]."
        first_token = kinds.index("token")
        assert kinds[first_token - 1] == "citations"

    async def test_without_a_listener_nothing_streams(self, monkeypatch):
        """The plain JSON endpoint passes no on_token: a non-streaming run."""
        pytest.importorskip("pydantic_ai")
        models_function = pytest.importorskip("pydantic_ai.models.function")
        messages_mod = pytest.importorskip("pydantic_ai.messages")
        model = models_function.FunctionModel(
            lambda m, i: messages_mod.ModelResponse(parts=[messages_mod.TextPart(content="Done.")])
        )
        result = await run_agent(db=None, question="Hi", org_id=uuid.uuid4(),
                                 project_id=uuid.uuid4(), model=model)
        assert result["answer"] == "Done."


def section(content, index=0, vector=None, side="a"):
    return {"chunk_id": uuid.uuid4(), "document_id": side, "filename": f"{side}.pdf",
            "chunk_index": index, "page_number": index + 1, "content": content, "vector": vector}


class TestCompareService:
    def test_lexical_alignment_pairs_changes_and_finds_unmatched(self):
        a = [section("The notice period is thirty days for termination by either party", 0),
             section("Liability is capped at the annual fees paid", 1),
             section("Payment is due within fourteen days of invoice", 2)]
        b = [section("The notice period is sixty days for termination by either party", 0, side="b"),
             section("Payment is due within fourteen days of invoice", 1, side="b"),
             section("Customer data is deleted within ninety days of exit", 2, side="b")]
        result = compare_module.align(a, b)
        assert result["method"] == "lexical"
        assert [p[0]["content"][:10] for p in result["pairs"]] == ["The notice"]
        assert result["identical"] == 1
        assert [s["content"][:9] for s in result["only_a"]] == ["Liability"]
        assert [s["content"][:8] for s in result["only_b"]] == ["Customer"]

    def test_embedding_alignment_is_used_when_every_section_has_a_vector(self):
        a = [section("x", vector=[1.0, 0.0])]
        b = [section("y", vector=[0.9, 0.1], side="b")]
        assert compare_module.align(a, b)["method"] == "embedding"

    def test_reply_is_validated(self):
        citations = [{"side": "a", "number": 1}, {"side": "b", "number": 1}]
        reply = """Here you go:
        {"summary": "One change.", "differences": [
          {"kind": "changed", "topic": "Notice", "detail": "30 vs 60 days", "a": ["A1", 9], "b": [1]},
          {"kind": "invented", "topic": "x", "detail": "y"},
          {"kind": "added", "topic": "", "detail": ""}
        ]}"""
        parsed = compare_module.parse_reply(reply, citations)
        assert parsed["summary"] == "One change."
        assert parsed["differences"] == [
            {"kind": "changed", "topic": "Notice", "detail": "30 vs 60 days", "a": [1], "b": [1]}
        ]
        assert compare_module.parse_reply("not json at all", citations) is None

    def test_excerpts_are_numbered_once_and_wrapped(self):
        shared = section("Same clause text here", 0)
        alignment = {"pairs": [(shared, section("Other words", 0, side="b"), 0.5)],
                     "only_a": [shared], "only_b": [], "identical": 0}
        citations, prompt = compare_module.number_excerpts(alignment)
        assert [c["label"] for c in citations] == ["A1", "B1"]
        assert prompt.count("EXCERPT A1") == 2 and "<document_excerpt" in prompt

    def test_heuristic_diff_describes_the_alignment(self):
        a, b = section("old clause", 0), section("new clause", 0, side="b")
        extra = section("brand new section", 1, side="b")
        alignment = {"pairs": [(a, b, 0.4)], "only_a": [], "only_b": [extra], "identical": 2}
        citations, _ = compare_module.number_excerpts(alignment)
        diff = compare_module.heuristic_diff(alignment, citations)
        assert [d["kind"] for d in diff["differences"]] == ["changed", "added"]
        assert diff["differences"][1]["b"] == [2] and "2 matched" in diff["summary"]


async def _ready_documents(env, *texts):
    ids = []
    for index, text in enumerate(texts):
        document = await env.upload(name=f"doc{index}.txt", data=text.encode() * 3)
        await env.set_document(document["id"], status="completed")
        async with env.factory() as session:
            from app.models import Chunk

            session.add(Chunk(document_id=uuid.UUID(document["id"]), content=text,
                              chunk_index=0, page_number=1, meta_data={}))
            await session.commit()
        ids.append(document["id"])
    return ids


class TestCompareApi:
    async def test_demo_mode_returns_a_structured_diff(self, env):
        a, b = await _ready_documents(
            env,
            "The notice period is thirty days for termination by either party.",
            "The notice period is sixty days for termination by either party.",
        )
        response = await env.client.post(f"{API}/compare", headers=env.headers,
                                          json={"document_a": a, "document_b": b})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["model"] == "mock" and body["alignment"] in ("lexical", "embedding")
        assert body["differences"] and body["differences"][0]["kind"] == "changed"
        assert {c["side"] for c in body["citations"]} == {"a", "b"}

    async def test_the_model_reply_becomes_the_diff_and_is_metered(self, env, monkeypatch):
        a, b = await _ready_documents(env, "Fee is ten dollars per seat each month.",
                                      "Fee is twelve dollars per seat each month.")
        monkeypatch.setattr(LLMService, "is_keyless", staticmethod(lambda: False))

        async def complete(messages):
            assert "DOCUMENT A: doc0.txt" in messages[1]["content"]
            return {"text": '{"summary": "Price rose.", "differences": [{"kind": "changed",'
                            ' "topic": "Fee", "detail": "10 to 12", "a": [1], "b": [1]}]}',
                    "tokens_used": 30, "prompt_tokens": 25, "completion_tokens": 5,
                    "latency": 0.1, "model": "test-model"}

        monkeypatch.setattr(LLMService, "complete", staticmethod(complete))
        response = await env.client.post(f"{API}/compare", headers=env.headers,
                                          json={"document_a": a, "document_b": b})
        assert response.status_code == 200, response.text
        assert response.json()["summary"] == "Price rose."
        from app.models import UsageEvent

        assert await env.count(UsageEvent, UsageEvent.mode == "compare") == 1

    async def test_refusals(self, env):
        a, b = await _ready_documents(env, "one text here please", "two text here please")
        same = await env.client.post(f"{API}/compare", headers=env.headers,
                                      json={"document_a": a, "document_b": a})
        assert same.status_code == 422
        await env.set_document(b, status="processing")
        busy = await env.client.post(f"{API}/compare", headers=env.headers,
                                      json={"document_a": a, "document_b": b})
        assert busy.status_code == 409
        other = await env.sign_up()
        foreign = await env.client.post(f"{API}/compare", headers=other,
                                         json={"document_a": a, "document_b": b})
        assert foreign.status_code == 404


class TestPromptsApi:
    async def test_builtins_personal_and_org(self, env):
        listed = (await env.client.get(f"{API}/prompts", headers=env.headers)).json()
        assert [p["scope"] for p in listed] == ["builtin"] * len(listed)
        assert all(p["id"].startswith("builtin:") and not p["editable"] for p in listed)

        mine = await env.client.post(f"{API}/prompts", headers=env.headers,
                                      json={"title": "  My   prompt ", "body": "Do the thing"})
        assert mine.status_code == 201 and mine.json()["title"] == "My prompt"
        shared = await env.client.post(f"{API}/prompts", headers=env.headers,
                                        json={"title": "Team", "body": "Shared", "scope": "org"})
        assert shared.status_code == 201
        scopes = [p["scope"] for p in (await env.client.get(f"{API}/prompts", headers=env.headers)).json()]
        assert scopes[-2:] == ["personal", "org"]

    async def test_viewers_cannot_touch_the_org_library(self, env):
        shared = (await env.client.post(f"{API}/prompts", headers=env.headers,
                                         json={"title": "Team", "body": "Shared", "scope": "org"})).json()
        await env.demote(env.headers)
        assert (await env.client.post(f"{API}/prompts", headers=env.headers,
                                       json={"title": "x", "body": "y", "scope": "org"})).status_code == 403
        assert (await env.client.patch(f"{API}/prompts/{shared['id']}", headers=env.headers,
                                        json={"title": "z"})).status_code == 403
        assert (await env.client.post(f"{API}/prompts", headers=env.headers,
                                       json={"title": "x", "body": "y"})).status_code == 201

    async def test_someone_elses_personal_prompt_is_invisible(self, env):
        mine = (await env.client.post(f"{API}/prompts", headers=env.headers,
                                       json={"title": "Secret", "body": "Mine"})).json()
        other = await env.sign_up()
        assert (await env.client.delete(f"{API}/prompts/{mine['id']}", headers=other)).status_code == 404
        listed = (await env.client.get(f"{API}/prompts", headers=other)).json()
        assert all(p["title"] != "Secret" for p in listed)
        assert (await env.client.delete(f"{API}/prompts/{mine['id']}", headers=env.headers)).status_code == 204

    async def test_blank_is_refused(self, env):
        response = await env.client.post(f"{API}/prompts", headers=env.headers,
                                          json={"title": "   ", "body": "x"})
        assert response.status_code == 422


async def _answer(env, searches):
    document = await env.upload()
    searches["results"].append(_chunk_result(document["id"]))
    session_id = await env.new_session()
    response = await env.client.post(f"{API}/chat/sessions/{session_id}/messages",
                                     headers=env.headers, json={"message": "Refunds?"})
    assert response.status_code == 200, response.text
    return session_id, response.json()


class TestSharing:
    async def test_answers_carry_a_confidence_verdict(self, env, searches):
        _, body = await _answer(env, searches)
        assert body["confidence"]["level"] in ("high", "medium", "low")
        assert body["assistant_message"]["confidence"] == body["confidence"]

    async def test_share_an_answer_publicly(self, env, searches):
        session_id, body = await _answer(env, searches)
        message_id = body["assistant_message"]["id"]
        created = await env.client.post(f"{API}/shares", headers=env.headers,
                                         json={"kind": "message", "target_id": message_id})
        assert created.status_code == 201, created.text
        token = created.json()["token"]
        assert created.json()["path"] == f"/share/{token}"

        async with env.factory() as session:
            link = (await session.execute(select(ShareLink))).scalar_one()
            assert link.token_hash == sharing.token_hash(token) and token not in link.token_hash

        public = await env.client.get(f"{API}/public/shares/{token}")
        assert public.status_code == 200
        assert public.headers["cache-control"] == "no-store"
        assert public.headers["referrer-policy"] == "no-referrer"
        view = public.json()
        assert [m["role"] for m in view["messages"]] == ["user", "assistant"]
        text = public.text
        for secret in (message_id, session_id, "@example.com", "chunk_id", "document_id", "org_id"):
            assert secret not in text

        listed = (await env.client.get(f"{API}/shares", headers=env.headers)).json()
        assert listed[0]["view_count"] == 1 and "token" not in listed[0]
        assert "share.create" in await env.audit_actions()

    async def test_revoked_expired_and_deleted_are_all_404(self, env, searches):
        session_id, _ = await _answer(env, searches)
        make = lambda: env.client.post(f"{API}/shares", headers=env.headers,  # noqa: E731
                                       json={"kind": "session", "target_id": session_id})
        revoked = (await make()).json()
        assert (await env.client.delete(f"{API}/shares/{revoked['id']}", headers=env.headers)).status_code == 204
        assert (await env.client.get(f"{API}/public/shares/{revoked['token']}")).status_code == 404
        assert "share.revoke" in await env.audit_actions()

        expired = (await make()).json()
        async with env.factory() as session:
            link = await session.get(ShareLink, uuid.UUID(expired["id"]))
            link.expires_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
            await session.commit()
        assert (await env.client.get(f"{API}/public/shares/{expired['token']}")).status_code == 404

        live = (await make()).json()
        assert (await env.client.get(f"{API}/public/shares/{live['token']}")).status_code == 200
        await env.client.delete(f"{API}/chat/sessions/{session_id}", headers=env.headers)
        assert (await env.client.get(f"{API}/public/shares/{live['token']}")).status_code == 404
        assert (await env.client.get(f"{API}/public/shares/dms_nonsense")).status_code == 404

    async def test_only_the_owner_shares_a_conversation(self, env, searches):
        session_id, body = await _answer(env, searches)
        other = await env.sign_up()
        for kind, target in (("session", session_id), ("message", body["assistant_message"]["id"]),
                             ("project", env.project_id)):
            response = await env.client.post(f"{API}/shares", headers=other,
                                              json={"kind": kind, "target_id": target})
            assert response.status_code == 404, kind

    async def test_projects_need_an_editor_and_show_only_document_names(self, env):
        document = await env.upload()
        await env.set_document(document["id"], status="completed")
        created = await env.client.post(f"{API}/shares", headers=env.headers,
                                         json={"kind": "project", "target_id": env.project_id,
                                               "expires_in_days": 7})
        assert created.status_code == 201 and created.json()["expires_at"]
        view = (await env.client.get(f"{API}/public/shares/{created.json()['token']}")).json()
        assert view["title"] == "Handbooks"
        assert [d["filename"] for d in view["documents"]] == ["policy.txt"]
        assert document["id"] not in str(view)

        await env.demote(env.headers)
        refused = await env.client.post(f"{API}/shares", headers=env.headers,
                                         json={"kind": "project", "target_id": env.project_id})
        assert refused.status_code == 403

    async def test_the_shared_message_must_be_an_answer(self, env, searches):
        _, body = await _answer(env, searches)
        response = await env.client.post(f"{API}/shares", headers=env.headers,
                                          json={"kind": "message", "target_id": body["user_message"]["id"]})
        assert response.status_code == 404

    async def test_deleting_the_target_removes_its_links(self, env, searches):
        session_id, _ = await _answer(env, searches)
        await env.client.post(f"{API}/shares", headers=env.headers,
                              json={"kind": "session", "target_id": session_id})
        await env.client.delete(f"{API}/chat/sessions/{session_id}", headers=env.headers)
        assert await env.count(ShareLink) == 0
        assert await env.count(ChatSession) == 0 and await env.count(ChatMessage) == 0


def test_tokens_are_long_random_and_keyed():
    first, second = sharing.new_token(), sharing.new_token()
    assert first != second and first.startswith("dms_") and len(first) > 40
    assert sharing.token_hash(first) != sharing.token_hash(second)
    assert len(sharing.token_hash(first)) == 64
