"""
agent.py
========
WHAT THIS FILE DOES
-------------------
This is AGENT MODE -- the second way DocMinds can answer a question.

The normal "rag" mode (retrieval.py + llm.py) follows a FIXED recipe:

    embed the question -> fetch the 5 closest chunks -> ONE model call -> answer

That recipe is fast, cheap and predictable, but it cannot adapt. If the first
search misses, the answer is weak. A question with two halves ("compare the
leave policy in the 2023 handbook with the 2024 one") gets a single search
that matches neither half well.

Agent mode hands the model TOOLS instead of a fixed set of excerpts, and lets
it decide what to do:

    search_documents(query, document_id?)   semantic search, like rag mode
    list_documents()                        which files exist in this project
    read_document_section(document_id, ...) read consecutive chunks of one file

So for "compare X in file A with Y in file B" the model can list the documents,
search file A, search file B, and only then write the answer.

THE LOOP, STEP BY STEP
----------------------
    1. We send the question + the tool descriptions to the model.
    2. The model replies either with an ANSWER (done) or with TOOL CALLS
       ("please run search_documents with query='annual leave'").
    3. Pydantic AI runs those tools -- the Python functions below -- and sends
       their results back to the model.
    4. Repeat from 2, at most AGENT_MAX_STEPS model calls in total.

WHY PYDANTIC AI
---------------
Pydantic AI (https://ai.pydantic.dev) runs that loop for us. We write ordinary
typed Python functions; it turns their signatures and docstrings into the JSON
tool schema the model sees, validates the arguments the model sends back
(a hallucinated parameter becomes a polite retry, not a crash), and stops the
loop when a usage limit is hit. It is from the same team as Pydantic, which
this project already uses for every schema.

HOW CITATIONS STAY CORRECT
--------------------------
In rag mode the excerpts are numbered [1]..[5] once, and the model cites those
numbers. Here the model may run three searches, so we keep ONE registry for
the whole run: the first time any tool surfaces a chunk it gets the next
global number, and every tool shows excerpts to the model already labelled
with those numbers ("EXCERPT [7] ..."). The model's "[7]" therefore always
points at the same chunk in the citations list we return, no matter which
tool call it came from. A chunk seen twice keeps its first number.

SECURITY
--------
Every tool is scoped to the caller's org_id AND the chat session's project_id,
both taken from the authenticated request -- never from the model. The model
can choose WHAT to search for, but it has no way to choose WHERE.

GRACEFUL FALLBACKS (mirroring llm.py)
-------------------------------------
    * No GROQ_API_KEY, LLM_PROVIDER=mock, or pydantic-ai not installed:
      run one search ourselves and return the demo-mode answer, model="mock".
    * The run fails (rate limit, too many steps, network): return a readable
      message, model="error", and keep whatever citations were gathered.
"""

import asyncio
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.project import Project
from app.services.llm import LLMService
from app.services.retrieval import RetrievalService

# The exact sentence llm.SYSTEM_PROMPT tells the model to use when the
# documents do not contain the answer. Kept identical in both modes so the
# frontend (and anyone grepping logs) sees one consistent "not found" message.
NOT_FOUND_ANSWER = "I could not find that information in the uploaded documents."

# The most chunks read_document_section may return in one call. Ten chunks is
# roughly 5,000 tokens -- plenty for "summarise this section", while stopping
# the model from pulling a whole 300-page manual into its context in one go.
MAX_SECTION_CHUNKS = 10

# How many documents list_documents shows. A project with hundreds of files
# would otherwise flood the context window with a filename list.
MAX_LISTED_DOCUMENTS = 50

# ----------------------------------------------------------------------
# THE AGENT'S SYSTEM PROMPT
# ----------------------------------------------------------------------
# Rules 1-5 are the same grounding rules as llm.SYSTEM_PROMPT -- the agent is
# held to exactly the same honesty standard as the fixed pipeline. The extra
# section teaches it WHEN to reach for each tool, which is what an agent needs
# that a fixed pipeline does not.
# ----------------------------------------------------------------------
AGENT_SYSTEM_PROMPT = f"""You are a precise enterprise document analyst with
tools for searching a company's internal documents.

Follow these rules strictly:

1. Answer using ONLY information returned by your tools. Do not use outside
   knowledge, and do not guess.
2. If the tool results do not contain enough information to answer, reply
   exactly:
   "{NOT_FOUND_ANSWER}"
   Do not attempt a partial or speculative answer.
3. Never invent facts, figures, dates, names, or policies.
4. Every excerpt your tools return is labelled with a bracketed number, like
   EXCERPT [3]. Cite the excerpt you used for each claim with that exact
   number, like [3], immediately after the sentence it supports. Never invent
   a citation number that no tool returned.
5. Be concise and direct. Use short paragraphs or bullet points. Do not repeat
   the question back to the user, and do not add a preamble. Do not describe
   which tools you used.

How to use your tools:

- search_documents: your main tool. Call it with a short, specific search
  phrase rather than the whole question.
- For a question with several parts, or a comparison between documents,
  search SEPARATELY for each part.
- If a search returns nothing useful, rephrase it (synonyms, more specific
  terms) and search again before giving up.
- list_documents: use it when the question names a file, asks what documents
  exist, or you need a document_id to restrict a search to one file.
- read_document_section: use it to summarise a document or to read the text
  around an excerpt (each excerpt shows its document_id and chunk number).
"""


# ======================================================================
# STEP 1: THE PER-QUESTION STATE ("deps")
# ======================================================================


@dataclass
class AgentDeps:
    """
    Everything the tools need while answering ONE question.

    Pydantic AI calls this object "dependencies": it is created fresh for each
    run and handed to every tool through `ctx.deps`. Nothing here is shared
    between users or between questions.
    """

    # The open database session for this request.
    db: AsyncSession
    # THE SECURITY BOUNDARY. Taken from the logged-in user, never from the model.
    org_id: uuid.UUID
    # The chat session's project. Every tool is restricted to it.
    project_id: uuid.UUID
    # How many excerpts each search returns.
    top_k: int

    # Pydantic AI may run several tool calls from ONE model reply at the same
    # time (asyncio concurrency). A SQLAlchemy AsyncSession must never be used
    # by two coroutines at once -- it raises "This session is provisioning a
    # new connection; concurrent operations are not permitted". Every tool
    # therefore takes this lock around its database work, so the calls queue
    # up instead of colliding.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # The citation registry: chunk_id -> {"number": n, "chunk": {...}}.
    # An OrderedDict remembers insertion order, so iterating it later gives the
    # chunks in the order the agent first saw them -- which is exactly the
    # order of their global numbers.
    citations: "OrderedDict[Any, Dict[str, Any]]" = field(default_factory=OrderedDict)

    # The trace of tool calls, returned to the UI as AgentStep objects.
    steps: List[Dict[str, Any]] = field(default_factory=list)

    def register(self, chunks: List[Dict[str, Any]]) -> List[int]:
        """
        Give each chunk its GLOBAL citation number, and return those numbers.

        A chunk already registered by an earlier tool call keeps its original
        number, so "[2]" means the same passage for the whole answer.
        """
        numbers: List[int] = []
        for chunk in chunks:
            key = chunk["chunk_id"]
            if key not in self.citations:
                self.citations[key] = {
                    "number": len(self.citations) + 1,
                    "chunk": chunk,
                }
            numbers.append(self.citations[key]["number"])
        return numbers

    def citation_chunks(self) -> List[Dict[str, Any]]:
        """Every registered chunk, in number order, with its `number` attached."""
        return [
            {**entry["chunk"], "number": entry["number"]}
            for entry in self.citations.values()
        ]

    def add_step(self, tool: str, args: Dict[str, Any], summary: str) -> None:
        """Record one tool call for the UI's step-by-step trace."""
        self.steps.append({"tool": tool, "args": args, "summary": summary})


# ======================================================================
# STEP 2: THE TOOLS
# ----------------------------------------------------------------------
# These are plain async functions that take `deps` as their first argument.
# They are registered with Pydantic AI further down, but keeping them as
# ordinary functions means the tests (and the keyless demo fallback) can call
# them directly without any AI framework involved.
#
# Every tool returns a STRING, because that is what the model reads. Errors
# (bad id, unknown document) are returned as readable sentences rather than
# raised: the model can then correct itself on the next step, whereas an
# exception would abort the whole answer.
# ======================================================================


def _format_excerpt(number: int, chunk: Dict[str, Any]) -> str:
    """
    Render one chunk the way the model sees it.

    Same shape as llm.build_context_prompt's "EXCERPT [n] (source: ...)", plus
    the document_id and chunk number so the model can follow up with
    read_document_section on the same file.
    """
    filename = chunk.get("filename", "unknown file")
    page = chunk.get("page_number")
    location = f"{filename}, page {page}" if page else filename
    return (
        f"EXCERPT [{number}] (source: {location}; "
        f"document_id={chunk.get('document_id')}; chunk {chunk.get('chunk_index')}):\n"
        f"{chunk.get('content', '')}"
    )


# What the model reads when a tool's database query fails. Phrased so the
# model can carry on (retry, or answer from what it already has).
DB_ERROR_MESSAGE = (
    "The database query for this tool failed. Try the call again once, "
    "or answer from the excerpts you already have."
)


@asynccontextmanager
async def _tool_db(deps: AgentDeps):
    """
    Wrap ONE tool call's database work: the lock, then a SAVEPOINT.

    WHY A SAVEPOINT: if a query fails (statement timeout, bad vector,
    connection blip), Postgres marks the WHOLE transaction as aborted. The
    chat endpoint later commits the user's question and the assistant's answer
    in that same transaction, so one failed tool query would turn the request
    into a 500 and lose the question. `begin_nested()` opens a SAVEPOINT; on
    error only the work since the savepoint is rolled back and the outer
    transaction stays usable. The tool then catches the SQLAlchemyError and
    returns DB_ERROR_MESSAGE to the model.

    `deps.db` is None only in the unit tests, where every query is faked.
    """
    async with deps.lock:
        if deps.db is None:
            yield
        else:
            async with deps.db.begin_nested():
                yield


def _parse_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    """Turn the model's string into a UUID, or None if it is not a valid one."""
    if value is None:
        return None
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError):
        return None


async def search_documents_tool(
    deps: AgentDeps, query: str, document_id: Optional[str] = None
) -> str:
    """
    Semantic search over this project's documents -- the same search rag mode
    uses, optionally narrowed to a single document.
    """
    args: Dict[str, Any] = {"query": query}
    if document_id:
        args["document_id"] = document_id

    doc_uuid = None
    if document_id:
        doc_uuid = _parse_uuid(document_id)
        if doc_uuid is None:
            deps.add_step("search_documents", args, "Invalid document id")
            return (
                f"'{document_id}' is not a valid document id. "
                "Call list_documents to see the real ids."
            )

    # RetrievalService.search already enforces org_id (the tenant filter) and
    # supports project_id and document_id narrowing, so we reuse it as-is.
    try:
        async with _tool_db(deps):
            chunks = await RetrievalService.search(
                db=deps.db,
                query=query,
                org_id=deps.org_id,
                project_id=deps.project_id,
                document_id=doc_uuid,
                top_k=deps.top_k,
            )
    except SQLAlchemyError as exc:
        print(f"[Agent] search_documents failed: {type(exc).__name__}: {exc}")
        deps.add_step("search_documents", args, "Search failed (database error)")
        return DB_ERROR_MESSAGE

    if not chunks:
        deps.add_step("search_documents", args, "No matching excerpts")
        return "No matching excerpts were found for that search."

    numbers = deps.register(chunks)
    file_count = len({chunk["document_id"] for chunk in chunks})
    deps.add_step(
        "search_documents",
        args,
        f"Found {len(chunks)} excerpt{'s' if len(chunks) != 1 else ''} "
        f"from {file_count} document{'s' if file_count != 1 else ''}",
    )
    return "\n\n".join(
        _format_excerpt(number, chunk) for number, chunk in zip(numbers, chunks, strict=True)
    )


async def list_documents_tool(deps: AgentDeps) -> str:
    """List the documents in this project so the model can pick a file."""
    statement = (
        select(Document)
        .join(Project, Document.project_id == Project.id)
        # The same tenant filter as retrieval.py: org first, then project.
        .where(Project.org_id == deps.org_id)
        .where(Document.project_id == deps.project_id)
        .order_by(Document.created_at.desc())
        .limit(MAX_LISTED_DOCUMENTS)
    )
    try:
        async with _tool_db(deps):
            result = await deps.db.execute(statement)
            documents = result.scalars().all()
    except SQLAlchemyError as exc:
        print(f"[Agent] list_documents failed: {type(exc).__name__}: {exc}")
        deps.add_step("list_documents", {}, "Listing failed (database error)")
        return DB_ERROR_MESSAGE

    if not documents:
        deps.add_step("list_documents", {}, "No documents in this project")
        return "This project has no documents."

    lines = []
    for document in documents:
        meta = document.meta_data or {}
        pages = meta.get("page_count")
        page_text = f", {pages} pages" if pages else ""
        lines.append(
            f"- {document.filename} (document_id={document.id}; "
            f"type {document.file_type}{page_text}; status {document.status})"
        )

    deps.add_step(
        "list_documents",
        {},
        f"Listed {len(documents)} document{'s' if len(documents) != 1 else ''}",
    )
    return (
        "Documents in this project (only status 'completed' can be searched "
        "or read):\n" + "\n".join(lines)
    )


async def read_document_section_tool(
    deps: AgentDeps, document_id: str, start_chunk: int = 0, count: int = 5
) -> str:
    """Read `count` consecutive chunks of one document, starting at `start_chunk`."""
    # Clamp whatever the model asked for into a sane range.
    start_chunk = max(0, int(start_chunk))
    count = max(1, min(int(count), MAX_SECTION_CHUNKS))
    args = {"document_id": document_id, "start_chunk": start_chunk, "count": count}

    doc_uuid = _parse_uuid(document_id)
    if doc_uuid is None:
        deps.add_step("read_document_section", args, "Invalid document id")
        return (
            f"'{document_id}' is not a valid document id. "
            "Call list_documents to see the real ids."
        )

    try:
        async with _tool_db(deps):
            # First confirm the document is inside THIS org and THIS project. A
            # model that guessed (or was tricked into) another tenant's id gets
            # the same answer as for an id that does not exist at all.
            doc_result = await deps.db.execute(
                select(Document)
                .join(Project, Document.project_id == Project.id)
                .where(Document.id == doc_uuid)
                .where(Project.org_id == deps.org_id)
                .where(Document.project_id == deps.project_id)
            )
            document = doc_result.scalar_one_or_none()

            rows: List[Chunk] = []
            if document is not None and document.status == "completed":
                chunk_result = await deps.db.execute(
                    select(Chunk)
                    .where(Chunk.document_id == doc_uuid)
                    .where(Chunk.chunk_index >= start_chunk)
                    .order_by(Chunk.chunk_index)
                    .limit(count)
                )
                rows = list(chunk_result.scalars().all())
    except SQLAlchemyError as exc:
        print(f"[Agent] read_document_section failed: {type(exc).__name__}: {exc}")
        deps.add_step("read_document_section", args, "Read failed (database error)")
        return DB_ERROR_MESSAGE

    if document is None:
        deps.add_step("read_document_section", args, "Document not found")
        return "No document with that id exists in this project."
    if document.status != "completed":
        deps.add_step("read_document_section", args, "Document not processed yet")
        return f"{document.filename} is still '{document.status}' and cannot be read yet."
    if not rows:
        deps.add_step("read_document_section", args, "No chunks at that position")
        return f"{document.filename} has no chunks from position {start_chunk} onwards."

    chunks = [
        {
            "chunk_id": row.id,
            "document_id": document.id,
            "filename": document.filename,
            "page_number": row.page_number,
            "chunk_index": row.chunk_index,
            "content": row.content,
            # These chunks were READ, not ranked by a search, so there is no
            # similarity score. 0.0 marks them as "not from a search".
            "score": 0.0,
            "meta_data": row.meta_data,
        }
        for row in rows
    ]
    numbers = deps.register(chunks)
    deps.add_step(
        "read_document_section",
        args,
        f"Read {len(chunks)} chunk{'s' if len(chunks) != 1 else ''} of "
        f"{document.filename} (chunks {rows[0].chunk_index}-{rows[-1].chunk_index})",
    )
    return "\n\n".join(
        _format_excerpt(number, chunk) for number, chunk in zip(numbers, chunks, strict=True)
    )


# ======================================================================
# STEP 3: BUILD THE PYDANTIC AI AGENT  (once, lazily)
# ======================================================================


@lru_cache(maxsize=1)
def get_agent():
    """
    Create the Pydantic AI Agent and register the three tools on it.

    `@lru_cache(maxsize=1)` builds it on first use and then returns the same
    object forever -- an Agent holds no per-request state (that lives in
    AgentDeps), so one instance can safely serve every user.

    `pydantic_ai` is imported HERE rather than at the top of the file, so the
    rest of the application still starts even if the package is missing; only
    agent mode degrades to its demo fallback.

    No model is attached here. It is passed per run (see run_agent), which is
    what lets the tests drive this exact agent with a fake model.
    """
    from pydantic_ai import Agent, RunContext

    agent = Agent(
        deps_type=AgentDeps,
        output_type=str,
        # `instructions` (not `system_prompt`) because Pydantic AI re-sends
        # instructions on every run even when we pass earlier conversation
        # turns as message_history; a system_prompt would be skipped then.
        instructions=AGENT_SYSTEM_PROMPT,
        # If the model sends malformed tool arguments, let it try again once
        # instead of failing the whole answer.
        retries=1,
    )

    # Each wrapper's DOCSTRING is what the model reads as the tool's
    # description, and its type hints become the argument schema. Write them
    # for the model, not for a human developer.

    @agent.tool
    async def search_documents(
        ctx: RunContext[AgentDeps], query: str, document_id: Optional[str] = None
    ) -> str:
        """
        Semantic search over the project's documents. Returns numbered excerpts.

        Args:
            query: A short, specific search phrase, e.g. "annual leave days".
            document_id: Optional. Restrict the search to one document
                (an id from list_documents or from an earlier excerpt).
        """
        return await search_documents_tool(ctx.deps, query, document_id)

    @agent.tool
    async def list_documents(ctx: RunContext[AgentDeps]) -> str:
        """List every document in the project with its id, type, pages and status."""
        return await list_documents_tool(ctx.deps)

    @agent.tool
    async def read_document_section(
        ctx: RunContext[AgentDeps],
        document_id: str,
        start_chunk: int = 0,
        count: int = 5,
    ) -> str:
        """
        Read consecutive chunks of one document in order. Use it to summarise a
        file or to see the text around an excerpt.

        Args:
            document_id: The document's id.
            start_chunk: The chunk number to start from (0 = the beginning).
            count: How many chunks to read, at most 10.
        """
        return await read_document_section_tool(
            ctx.deps, document_id, start_chunk, count
        )

    return agent


def _groq_model():
    """The real model: Groq, through Pydantic AI's Groq integration."""
    from pydantic_ai.models.groq import GroqModel
    from pydantic_ai.providers.groq import GroqProvider

    return GroqModel(
        settings.AGENT_MODEL or settings.GROQ_MODEL,
        provider=GroqProvider(api_key=settings.GROQ_API_KEY),
    )


def to_model_messages(history: Optional[List[Dict[str, str]]], question: str) -> list:
    """
    Convert our chat history into Pydantic AI's message objects.

    build_history returns [{"role": "user"|"assistant", "content": ...}].
    Pydantic AI wants ModelRequest (what we sent) and ModelResponse (what the
    model replied) objects instead.

    Two details:
      * chat.py saves the current question BEFORE loading history, so the last
        entry is usually the question itself. We drop it, because the agent
        receives the question separately and would otherwise see it twice.
      * A conversation must start with a user turn, so a leading assistant
        message (possible after we keep only the last 6) is dropped too.
    """
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    turns = list(history or [])
    if turns and turns[-1].get("role") == "user" and turns[-1].get("content") == question:
        turns = turns[:-1]

    # Same window as llm.py: the last 6 messages (3 exchanges).
    turns = turns[-6:]
    while turns and turns[0].get("role") != "user":
        turns = turns[1:]

    messages: list = []
    for turn in turns:
        content = turn.get("content") or ""
        if turn.get("role") == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif turn.get("role") == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content=content)]))
    return messages


# ======================================================================
# STEP 4: RUN IT -- the one function chat.py calls
# ======================================================================


def _result(
    deps: AgentDeps, answer: str, tokens: int, started_at: float, model: str
) -> Dict[str, Any]:
    """The dictionary shape chat.py expects back from run_agent."""
    return {
        "answer": answer,
        "tokens_used": tokens,
        "latency": round(time.perf_counter() - started_at, 3),
        "model": model,
        "citations_chunks": deps.citation_chunks(),
        "steps": list(deps.steps),
    }


async def run_agent(
    db: AsyncSession,
    question: str,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    top_k: Optional[int] = None,
    history: Optional[List[Dict[str, str]]] = None,
    model: Any = None,
) -> Dict[str, Any]:
    """
    Answer one question in agent mode.

    Returns:
        {
          "answer": str, "tokens_used": int, "latency": float, "model": str,
          "citations_chunks": [chunk dict + "number", ...]   # global order
          "steps": [{"tool", "args", "summary"}, ...]
        }

    `model` exists for the tests: pass a Pydantic AI TestModel/FunctionModel
    to drive the real agent and tools without calling Groq.
    """
    started_at = time.perf_counter()
    deps = AgentDeps(
        db=db,
        org_id=org_id,
        project_id=project_id,
        top_k=top_k if top_k is not None else settings.RETRIEVAL_TOP_K,
    )

    # ---- Is the agent framework available at all? --------------------
    try:
        import pydantic_ai  # noqa: F401
        framework_available = True
    except ImportError:
        print("[Agent] pydantic-ai is not installed. Run: pip install -r requirements.txt")
        framework_available = False

    keyless = settings.LLM_PROVIDER == "mock" or not settings.GROQ_API_KEY
    if model is None and (keyless or not framework_available):
        # ---- DEMO FALLBACK -------------------------------------------
        # No model to drive the loop, so we play the agent's most likely first
        # move ourselves: one search with the question. The UI still gets a
        # real step and real citations, just like rag mode's demo answer.
        await search_documents_tool(deps, question)
        chunks = deps.citation_chunks()
        mock = LLMService._mock_answer(question, chunks, started_at)
        return _result(deps, mock["answer"], 0, started_at, "mock")

    try:
        from pydantic_ai.usage import UsageLimits

        agent = get_agent()
        run_model = model if model is not None else _groq_model()
        run = await agent.run(
            question,
            deps=deps,
            model=run_model,
            message_history=to_model_messages(history, question) or None,
            # The runaway-loop guard: at most AGENT_MAX_STEPS model calls.
            usage_limits=UsageLimits(request_limit=settings.AGENT_MAX_STEPS),
            model_settings={
                "temperature": settings.LLM_TEMPERATURE,
                "max_tokens": settings.LLM_MAX_TOKENS,
            },
        )

        answer = (run.output or "").strip() or NOT_FOUND_ANSWER
        # Recent Pydantic AI releases expose `usage` as a property; older 1.x
        # releases made it a method. Accept either so a version bump in
        # requirements.txt cannot silently break the token count.
        usage = run.usage
        if not hasattr(usage, "total_tokens") and callable(usage):
            usage = usage()
        tokens = getattr(usage, "total_tokens", 0) or 0
        model_name = (
            settings.AGENT_MODEL or settings.GROQ_MODEL
            if model is None
            else getattr(run_model, "model_name", "test")
        )
        return _result(deps, answer, tokens, started_at, model_name)

    except SQLAlchemyError:
        # The tools catch their own query errors inside a SAVEPOINT, so this
        # only happens if the session itself is broken. Pretending we can
        # still save an answer would fail at commit time anyway, so let the
        # endpoint's normal error handling (rollback, 500) deal with it.
        raise
    except Exception as exc:
        # Rate limit, UsageLimitExceeded, network failure, bad key... Surface a
        # readable message instead of a 500, and keep whatever the tools had
        # already found so the user still sees the sources.
        print(f"[Agent] Run failed: {type(exc).__name__}: {exc}")
        return _result(
            deps,
            (
                "The AI agent could not finish answering this question "
                f"({type(exc).__name__}). Any document excerpts it found are "
                "listed below as sources. Try again, or switch to Quick answer."
            ),
            0,
            started_at,
            "error",
        )
