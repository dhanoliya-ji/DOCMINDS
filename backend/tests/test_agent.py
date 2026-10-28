import uuid

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.models.chat import ChatMessage
from app.schemas.rag import AgentStep, ChatRequest
from app.services import agent as agent_module
from app.services.agent import (
    NOT_FOUND_ANSWER,
    AgentDeps,
    read_document_section_tool,
    run_agent,
    search_documents_tool,
)
from app.services.retrieval import RetrievalService

ORG = uuid.uuid4()
PROJECT = uuid.uuid4()
DOC_A = uuid.uuid4()
DOC_B = uuid.uuid4()


def chunk(content: str, document_id=DOC_A, filename="handbook.pdf", index=0) -> dict:
    """A retrieval result, shaped the way RetrievalService.search returns them."""
    return {
        "chunk_id": uuid.uuid4(),
        "document_id": document_id,
        "filename": filename,
        "page_number": 3,
        "chunk_index": index,
        "content": content,
        "score": 0.8,
        "meta_data": {},
    }


def make_deps(top_k: int = 5) -> AgentDeps:
    return AgentDeps(db=None, org_id=ORG, project_id=PROJECT, top_k=top_k)


@pytest.fixture
def fake_search(monkeypatch):
    """Replace RetrievalService.search with a scripted fake."""
    state = {"script": {}, "calls": []}

    async def search(**kwargs):
        state["calls"].append(kwargs)
        return list(state["script"].get(kwargs["query"], []))

    monkeypatch.setattr(RetrievalService, "search", staticmethod(search))
    return state


@pytest.fixture
def keyless(monkeypatch):
    """No API key -> run_agent must take its demo fallback."""
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")


class TestSchemas:
    def test_mode_defaults_to_rag(self):
        """Old clients send no mode and must keep getting the fixed pipeline."""
        assert ChatRequest(message="hi").mode == "rag"

    def test_agent_mode_accepted(self):
        assert ChatRequest(message="hi", mode="agent").mode == "agent"

    @pytest.mark.parametrize("bad", ["Agent", "deep", "", "RAG"])
    def test_other_modes_rejected(self, bad):
        with pytest.raises(ValidationError):
            ChatRequest(message="hi", mode=bad)

    def test_agent_step_shape(self):
        step = AgentStep(tool="search_documents", args={"query": "x"}, summary="ok")
        assert step.model_dump() == {
            "tool": "search_documents",
            "args": {"query": "x"},
            "summary": "ok",
        }


class TestCitationRegistry:
    def test_numbers_are_global_and_first_seen(self):
        deps = make_deps()
        a, b, c = chunk("a"), chunk("b"), chunk("c")

        assert deps.register([a, b]) == [1, 2]
        assert deps.register([b, c]) == [2, 3]

    def test_duplicate_keeps_first_number_and_is_stored_once(self):
        deps = make_deps()
        a = chunk("a")
        deps.register([a])
        deps.register([a, a])

        chunks = deps.citation_chunks()
        assert len(chunks) == 1
        assert chunks[0]["number"] == 1

    def test_citation_chunks_in_number_order(self):
        deps = make_deps()
        first, second, third = chunk("1"), chunk("2"), chunk("3")
        deps.register([first])
        deps.register([second, first, third])

        out = deps.citation_chunks()
        assert [c["number"] for c in out] == [1, 2, 3]
        assert [c["content"] for c in out] == ["1", "2", "3"]


class TestSearchTool:
    async def test_excerpts_carry_global_numbers(self, fake_search):
        shared = chunk("shared text")
        fake_search["script"] = {
            "leave": [chunk("leave text"), shared],
            "sick": [shared, chunk("sick text", document_id=DOC_B, filename="b.pdf")],
        }
        deps = make_deps()

        first = await search_documents_tool(deps, "leave")
        second = await search_documents_tool(deps, "sick")

        assert "EXCERPT [1]" in first and "EXCERPT [2]" in first
        assert "EXCERPT [2]" in second and "EXCERPT [3]" in second
        assert "EXCERPT [4]" not in second
        assert len(deps.citation_chunks()) == 3

    async def test_scoped_to_org_and_project(self, fake_search):
        deps = make_deps(top_k=7)
        await search_documents_tool(deps, "anything", document_id=str(DOC_A))

        call = fake_search["calls"][0]
        assert call["org_id"] == ORG
        assert call["project_id"] == PROJECT
        assert call["document_id"] == DOC_A
        assert call["top_k"] == 7

    async def test_records_a_step(self, fake_search):
        fake_search["script"] = {
            "q": [chunk("x"), chunk("y", document_id=DOC_B)],
        }
        deps = make_deps()
        await search_documents_tool(deps, "q")

        assert deps.steps == [
            {
                "tool": "search_documents",
                "args": {"query": "q"},
                "summary": "Found 2 excerpts from 2 documents",
            }
        ]

    async def test_no_results_is_a_message_not_an_error(self, fake_search):
        deps = make_deps()
        text = await search_documents_tool(deps, "nothing matches")
        assert "No matching excerpts" in text
        assert deps.steps[0]["summary"] == "No matching excerpts"

    async def test_invalid_document_id_never_reaches_retrieval(self, fake_search):
        deps = make_deps()
        text = await search_documents_tool(deps, "q", document_id="not-a-uuid")

        assert "not a valid document id" in text
        assert fake_search["calls"] == []


class TestReadSectionTool:
    async def test_invalid_id_returns_readable_message(self):
        deps = make_deps()
        text = await read_document_section_tool(deps, "handbook.pdf")
        assert "not a valid document id" in text
        assert deps.steps[0]["tool"] == "read_document_section"

    async def test_count_is_capped(self):
        deps = make_deps()
        await read_document_section_tool(deps, "bad", start_chunk=-4, count=500)
        args = deps.steps[0]["args"]
        assert args["count"] == agent_module.MAX_SECTION_CHUNKS
        assert args["start_chunk"] == 0


class TestKeylessFallback:
    async def test_demo_answer_with_real_step_and_citations(self, keyless, fake_search):
        fake_search["script"] = {"How much leave?": [chunk("25 days of leave.")]}

        result = await run_agent(
            db=None, question="How much leave?", org_id=ORG, project_id=PROJECT
        )

        assert result["model"] == "mock"
        assert "Demo mode" in result["answer"]
        assert result["tokens_used"] == 0
        assert [s["tool"] for s in result["steps"]] == ["search_documents"]
        assert result["citations_chunks"][0]["number"] == 1
        assert set(result) == {
            "answer", "tokens_used", "prompt_tokens", "completion_tokens",
            "latency", "model", "citations_chunks", "steps",
        }

    async def test_nothing_found_uses_exact_fallback_sentence(self, keyless, fake_search):
        result = await run_agent(
            db=None, question="unanswerable", org_id=ORG, project_id=PROJECT
        )
        assert result["answer"] == NOT_FOUND_ANSWER
        assert result["citations_chunks"] == []

    async def test_mock_provider_also_falls_back(self, monkeypatch, fake_search):
        monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_present")
        monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
        result = await run_agent(db=None, question="q", org_id=ORG, project_id=PROJECT)
        assert result["model"] == "mock"

    async def test_top_k_defaults_from_settings(self, keyless, fake_search):
        await run_agent(db=None, question="q", org_id=ORG, project_id=PROJECT)
        assert fake_search["calls"][0]["top_k"] == settings.RETRIEVAL_TOP_K


class TestHistoryConversion:
    def test_roles_map_to_request_and_response(self):
        messages_mod = pytest.importorskip("pydantic_ai.messages")
        history = [
            {"role": "user", "content": "first q"},
            {"role": "assistant", "content": "first a"},
        ]
        out = agent_module.to_model_messages(history, "new question")

        assert isinstance(out[0], messages_mod.ModelRequest)
        assert out[0].parts[0].content == "first q"
        assert isinstance(out[1], messages_mod.ModelResponse)
        assert out[1].parts[0].content == "first a"

    def test_current_question_is_not_duplicated(self):
        pytest.importorskip("pydantic_ai")
        history = [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "now?"},
        ]
        out = agent_module.to_model_messages(history, "now?")
        assert len(out) == 2

    def test_keeps_last_six_starting_with_user(self):
        pytest.importorskip("pydantic_ai")
        history = []
        for i in range(5):
            history += [
                {"role": "user", "content": f"q{i}"},
                {"role": "assistant", "content": f"a{i}"},
            ]
        out = agent_module.to_model_messages(history, "new")

        assert len(out) == 6
        assert out[0].parts[0].content == "q2"

    def test_leading_assistant_turn_dropped(self):
        pytest.importorskip("pydantic_ai")
        history = [
            {"role": "assistant", "content": "orphan"},
            {"role": "user", "content": "q"},
        ]
        out = agent_module.to_model_messages(history, "new")
        assert len(out) == 1
        assert out[0].parts[0].content == "q"

    def test_empty_history(self):
        pytest.importorskip("pydantic_ai")
        assert agent_module.to_model_messages(None, "q") == []


def _scripted_model(queries, final_text):
    """A FunctionModel that issues one search_documents call per query, then"""
    models_function = pytest.importorskip("pydantic_ai.models.function")
    messages_mod = pytest.importorskip("pydantic_ai.messages")

    def respond(messages, info):
        returned = sum(
            1
            for message in messages
            for part in getattr(message, "parts", [])
            if isinstance(part, messages_mod.ToolReturnPart)
        )
        if returned < len(queries):
            return messages_mod.ModelResponse(
                parts=[
                    messages_mod.ToolCallPart(
                        tool_name="search_documents",
                        args={"query": queries[returned]},
                        tool_call_id=f"call-{returned}",
                    )
                ]
            )
        return messages_mod.ModelResponse(parts=[messages_mod.TextPart(content=final_text)])

    return models_function.FunctionModel(respond)


class TestAgentRun:
    async def test_multi_search_answer_and_citations(self, fake_search, monkeypatch):
        pytest.importorskip("pydantic_ai")
        monkeypatch.setattr(settings, "AGENT_MAX_STEPS", 6)

        shared = chunk("shared")
        fake_search["script"] = {
            "policy A": [chunk("A says 20 days"), shared],
            "policy B": [shared, chunk("B says 25 days", document_id=DOC_B)],
        }
        model = _scripted_model(["policy A", "policy B"], "A is 20 [1], B is 25 [3].")

        result = await run_agent(
            db=None,
            question="Compare A and B",
            org_id=ORG,
            project_id=PROJECT,
            history=[{"role": "user", "content": "Compare A and B"}],
            model=model,
        )

        assert result["answer"] == "A is 20 [1], B is 25 [3]."
        assert [s["args"]["query"] for s in result["steps"]] == ["policy A", "policy B"]
        numbered = {c["number"]: c["content"] for c in result["citations_chunks"]}
        assert numbered == {1: "A says 20 days", 2: "shared", 3: "B says 25 days"}
        assert result["model"] not in ("mock", "error")

    async def test_tool_results_seen_by_model_are_numbered(self, fake_search):
        pytest.importorskip("pydantic_ai")
        messages_mod = pytest.importorskip("pydantic_ai.messages")
        models_function = pytest.importorskip("pydantic_ai.models.function")
        fake_search["script"] = {"q": [chunk("alpha"), chunk("beta")]}
        seen = []

        def respond(messages, info):
            returns = [
                part.content
                for message in messages
                for part in getattr(message, "parts", [])
                if isinstance(part, messages_mod.ToolReturnPart)
            ]
            if not returns:
                return messages_mod.ModelResponse(parts=[messages_mod.ToolCallPart(
                    tool_name="search_documents", args={"query": "q"}, tool_call_id="c1",
                )])
            seen.extend(returns)
            return messages_mod.ModelResponse(parts=[messages_mod.TextPart(content="ok [2]")])

        await run_agent(
            db=None, question="q", org_id=ORG, project_id=PROJECT,
            model=models_function.FunctionModel(respond),
        )
        assert "EXCERPT [1]" in seen[0] and "EXCERPT [2]" in seen[0]

    async def test_step_limit_returns_error_but_keeps_sources(self, fake_search, monkeypatch):
        pytest.importorskip("pydantic_ai")
        monkeypatch.setattr(settings, "AGENT_MAX_STEPS", 2)
        fake_search["script"] = {f"q{i}": [chunk(f"text {i}")] for i in range(10)}

        model = _scripted_model([f"q{i}" for i in range(10)], "never reached")
        result = await run_agent(
            db=None, question="loop", org_id=ORG, project_id=PROJECT, model=model
        )

        assert result["model"] == "error"
        assert "could not finish" in result["answer"]
        assert result["steps"], "tool calls made before the limit must be kept"
        assert result["citations_chunks"]

    async def test_agent_registers_all_three_tools(self):
        pytest.importorskip("pydantic_ai")
        test_module = pytest.importorskip("pydantic_ai.models.test")

        model = test_module.TestModel(call_tools=[])
        await agent_module.get_agent().run("hi", deps=make_deps(), model=model)

        names = {tool.name for tool in model.last_model_request_parameters.function_tools}
        assert names == {"search_documents", "list_documents", "read_document_section"}


class FakeSession:
    """Just enough of an AsyncSession for ask_question."""

    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        import datetime

        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.datetime.now(datetime.timezone.utc)


@pytest.fixture
def chat_client(monkeypatch):
    """An HTTP client for the real FastAPI app with auth, the DB session, the"""
    from types import SimpleNamespace

    from httpx import ASGITransport, AsyncClient

    from app.api import deps as api_deps
    from app.api.v1 import chat as chat_module
    from app.main import app

    session_id = uuid.uuid4()
    fake_db = FakeSession()
    user = SimpleNamespace(id=uuid.uuid4(), org_id=ORG, is_active=True)
    chat_session = SimpleNamespace(id=session_id, project_id=PROJECT, title="Existing")

    async def get_db():
        yield fake_db

    async def owned_session(db, sid, current_user):
        return chat_session

    async def no_history(db, sid, limit=6):
        return []

    agent_chunks = [
        {**chunk("Leave is 25 days."), "number": 3},
        {**chunk("Carry-over is 5 days.", document_id=DOC_B, filename="policy.pdf"), "number": 7},
    ]
    state = {"agent_calls": 0}

    async def fake_run_agent(**kwargs):
        state["agent_calls"] += 1
        return {
            "answer": "Leave is 25 days [3], with 5 carried over [7].",
            "tokens_used": 42,
            "latency": 0.1,
            "model": "test",
            "citations_chunks": agent_chunks,
            "steps": [{"tool": "search_documents", "args": {"query": "leave"},
                       "summary": "Found 2 excerpts from 2 documents"}],
        }

    async def fake_search(**kwargs):
        return [chunk("Leave is 25 days.")]

    async def fake_generate_answer(question, chunks, history):
        return {"answer": "Leave is 25 days [1].", "tokens_used": 10,
                "latency": 0.1, "model": "test"}

    async def within_quota(db, org_id, now=None):
        return None

    monkeypatch.setattr(chat_module, "_get_owned_session", owned_session)
    monkeypatch.setattr(chat_module, "run_agent", fake_run_agent)
    monkeypatch.setattr(chat_module.usage, "check_token_quota", within_quota)
    monkeypatch.setattr(RetrievalService, "build_history", staticmethod(no_history))
    monkeypatch.setattr(RetrievalService, "search", staticmethod(fake_search))
    monkeypatch.setattr(chat_module.LLMService, "generate_answer", staticmethod(fake_generate_answer))
    app.dependency_overrides[api_deps.get_db] = get_db
    app.dependency_overrides[api_deps.get_current_active_user] = lambda: user

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    url = f"{settings.API_V1_STR}/chat/sessions/{session_id}/messages"
    yield SimpleNamespace(client=client, url=url, state=state, db=fake_db)
    app.dependency_overrides.clear()


class TestChatEndpointModes:
    async def test_agent_mode_uses_global_numbers_and_returns_steps(self, chat_client):
        async with chat_client.client as client:
            response = await client.post(
                chat_client.url, json={"message": "How much leave?", "mode": "agent"}
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mode"] == "agent"
        assert [c["number"] for c in body["citations"]] == [3, 7]
        assert len(body["steps"]) == 1
        assert body["steps"][0]["tool"] == "search_documents"
        assert chat_client.state["agent_calls"] == 1
        assistant = next(
            obj for obj in chat_client.db.added
            if isinstance(obj, ChatMessage) and obj.role == "assistant"
        )
        assert [c["number"] for c in assistant.citations] == [3, 7]
        assert chat_client.db.committed

    async def test_rag_mode_is_default_with_no_steps(self, chat_client):
        async with chat_client.client as client:
            response = await client.post(chat_client.url, json={"message": "How much leave?"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mode"] == "rag"
        assert body["steps"] == []
        assert [c["number"] for c in body["citations"]] == [1]
        assert chat_client.state["agent_calls"] == 0, "rag mode must not run the agent"


class TestToolDatabaseErrors:
    """A failed query inside a tool must roll back only that tool call (a"""

    async def test_search_db_error_is_a_message_inside_a_savepoint(self, monkeypatch):
        from sqlalchemy.exc import OperationalError

        events = []

        class Savepoint:
            async def __aenter__(self):
                events.append("savepoint")

            async def __aexit__(self, exc_type, exc, tb):
                events.append("rollback" if exc_type else "release")
                return False

        class Db:
            def begin_nested(self):
                return Savepoint()

        async def failing_search(**kwargs):
            raise OperationalError("SELECT ...", {}, Exception("statement timeout"))

        monkeypatch.setattr(RetrievalService, "search", staticmethod(failing_search))
        deps = AgentDeps(db=Db(), org_id=ORG, project_id=PROJECT, top_k=5)

        text = await search_documents_tool(deps, "leave")

        assert text == agent_module.DB_ERROR_MESSAGE
        assert events == ["savepoint", "rollback"]
        assert deps.steps[0]["summary"] == "Search failed (database error)"
        assert deps.citation_chunks() == []
