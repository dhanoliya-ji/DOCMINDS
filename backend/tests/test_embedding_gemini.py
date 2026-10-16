"""
test_embedding_gemini.py
========================
WHAT THIS FILE TESTS
--------------------
The Gemini provider in `services/embedding.py` -- the one the free live demo
uses instead of the local PyTorch model.

Most tests replace the network with an `httpx.MockTransport`, so they check
exactly what is SENT (model, task type, size, key header) and how replies and
failures are HANDLED (order, unit length, batching, retries) without a key.

One test calls the real API. It runs only when GEMINI_API_KEY is set -- CI
passes the repository's key -- and skips everywhere else.

TIER 1: no database.
"""
import json
import math
import os

import httpx
import pytest

from app.core.config import settings
from app.services.embedding import EmbeddingService

DIMENSION = settings.EMBEDDING_DIMENSION


@pytest.fixture
def gemini(monkeypatch):
    """Point the provider at a fake Gemini; returns the list of requests seen."""
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("app.services.embedding.time.sleep", lambda _s: None)
    seen = []

    def install(handler):
        def recording(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(EmbeddingService, "_gemini_transport", httpx.MockTransport(recording))
        return seen

    return install


def _echo(request: httpx.Request) -> httpx.Response:
    """Reply like Gemini: one un-normalised vector per text, encoding its index."""
    body = json.loads(request.content)
    embeddings = [
        {"values": [float(i + 1)] + [2.0] * (DIMENSION - 1)} for i, _ in enumerate(body["requests"])
    ]
    return httpx.Response(200, json={"embeddings": embeddings})


def test_documents_are_embedded_as_retrieval_documents(gemini):
    seen = gemini(_echo)
    EmbeddingService.get_embeddings(["first passage", "second passage"])

    (request,) = seen
    body = json.loads(request.content)
    assert request.headers["x-goog-api-key"] == "test-key"
    assert request.url.path.endswith(f"/models/{settings.GEMINI_EMBEDDING_MODEL}:batchEmbedContents")
    assert [r["content"]["parts"][0]["text"] for r in body["requests"]] == ["first passage", "second passage"]
    assert {r["taskType"] for r in body["requests"]} == {"RETRIEVAL_DOCUMENT"}
    assert {r["outputDimensionality"] for r in body["requests"]} == {DIMENSION}


def test_a_question_is_embedded_as_a_retrieval_query(gemini):
    seen = gemini(_echo)
    EmbeddingService.get_embedding("what is the refund policy?")

    assert json.loads(seen[0].content)["requests"][0]["taskType"] == "RETRIEVAL_QUERY"


def test_vectors_come_back_in_order_and_unit_length(gemini):
    gemini(_echo)
    vectors = EmbeddingService.get_embeddings(["a", "b", "c"])

    assert len(vectors) == 3
    assert all(len(v) == DIMENSION for v in vectors)
    for v in vectors:
        assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)
    # The first component encodes the reply index, so order survives normalising.
    assert vectors[0][0] < vectors[1][0] < vectors[2][0]


def test_large_inputs_are_sent_in_batches_of_100(gemini):
    seen = gemini(_echo)
    vectors = EmbeddingService.get_embeddings([f"chunk {i}" for i in range(250)])

    assert [len(json.loads(r.content)["requests"]) for r in seen] == [100, 100, 50]
    assert len(vectors) == 250


def test_the_free_tier_rate_limit_is_retried(gemini):
    replies = iter([httpx.Response(429, headers={"retry-after": "0"}), None])

    def handler(request):
        reply = next(replies)
        return reply if reply is not None else _echo(request)

    seen = gemini(handler)
    assert len(EmbeddingService.get_embeddings(["x"])) == 1
    assert len(seen) == 2


def test_a_rejected_key_fails_at_once_instead_of_storing_fake_vectors(gemini):
    seen = gemini(lambda _r: httpx.Response(403, json={"error": {"message": "API key not valid"}}))

    with pytest.raises(RuntimeError, match="HTTP 403"):
        EmbeddingService.get_embeddings(["x"])
    assert len(seen) == 1  # retrying cannot fix a bad key


def test_a_missing_key_is_a_clear_error(gemini, monkeypatch):
    gemini(_echo)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        EmbeddingService.get_embeddings(["x"])


def test_the_stored_model_name_includes_the_size(gemini):
    assert EmbeddingService.current_model_name() == f"{settings.GEMINI_EMBEDDING_MODEL}@{DIMENSION}"


@pytest.mark.gemini_live
@pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="needs a real GEMINI_API_KEY")
def test_the_real_gemini_api(monkeypatch):
    """One real round trip: right size, unit length, and actually semantic."""
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", os.environ["GEMINI_API_KEY"])
    monkeypatch.setattr(EmbeddingService, "_gemini_transport", None)

    refunds, weather = EmbeddingService.get_embeddings(
        [
            "Customers may return any item within 30 days for a full refund.",
            "Tomorrow will be sunny with light winds in the afternoon.",
        ]
    )
    question = EmbeddingService.get_embedding("How long do I have to get my money back?")

    for v in (refunds, weather, question):
        assert len(v) == DIMENSION
        assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-6)

    def cosine(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(question, refunds) > cosine(question, weather)
