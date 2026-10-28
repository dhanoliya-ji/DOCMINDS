import json
import math
import os

import httpx
import pytest

from app.core.config import settings
from app.services import embedding as embedding_module
from app.services.embedding import EmbeddingRateLimited, EmbeddingService, gemini_retry_hint
from app.services.pacing import RatePacer

DIMENSION = settings.EMBEDDING_DIMENSION


@pytest.fixture
def gemini(monkeypatch):
    """Point the provider at a fake Gemini; returns the list of requests seen."""
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    clock = FakeClock()
    monkeypatch.setattr(embedding_module, "_now", clock.now)
    monkeypatch.setattr(embedding_module, "_sleep", clock.sleep)
    monkeypatch.setattr(embedding_module, "gemini_pacer", RatePacer(clock=clock.now, sleep=clock.sleep))
    monkeypatch.setattr(embedding_module.random, "uniform", lambda _a, b: b)
    seen = []

    def install(handler):
        def recording(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(EmbeddingService, "_gemini_transport", httpx.MockTransport(recording))
        return seen

    install.clock = clock
    return install


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


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
    assert vectors[0][0] < vectors[1][0] < vectors[2][0]


def test_large_inputs_are_sent_in_batches_of_at_most_100(gemini, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_EMBED_REQUESTS_PER_MINUTE", 1000)
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


def test_a_batch_never_holds_more_than_a_minutes_requests(gemini, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_EMBED_REQUESTS_PER_MINUTE", 30)
    seen = gemini(_echo)
    EmbeddingService.get_embeddings([f"chunk {i}" for i in range(70)])

    assert [len(json.loads(r.content)["requests"]) for r in seen] == [30, 30, 10]


def test_batches_are_split_by_tokens_so_one_request_fits_the_quota(gemini, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_EMBED_TOKENS_PER_MINUTE", 10_000)
    seen = gemini(_echo)
    EmbeddingService.get_embeddings(["word " * 500] * 40)

    budget = settings.GEMINI_EMBED_TOKENS_PER_MINUTE // 2
    sizes = [len(json.loads(r.content)["requests"]) for r in seen]
    assert sum(sizes) == 40 and len(sizes) > 1
    for request in seen:
        texts = [r["content"]["parts"][0]["text"] for r in json.loads(request.content)["requests"]]
        assert sum(EmbeddingService._estimate_tokens(t) for t in texts) <= budget


def test_requests_are_paced_to_the_per_minute_limit(gemini, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_EMBED_REQUESTS_PER_MINUTE", 20)
    seen = gemini(_echo)
    EmbeddingService.get_embeddings([f"chunk {i}" for i in range(60)])

    assert len(seen) == 3
    waits = gemini.clock.sleeps
    assert len(waits) == 2 and all(55 <= w <= 61 for w in waits)


def test_a_question_does_not_queue_behind_a_document(gemini, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_EMBED_REQUESTS_PER_MINUTE", 5)
    gemini(_echo)
    EmbeddingService.get_embeddings([f"chunk {i}" for i in range(5)])  # minute used up
    EmbeddingService.get_embedding("urgent question?")

    assert gemini.clock.sleeps == []


def _quota(delay=None, header=None):
    body = {"error": {"code": 429, "message": "Resource exhausted"}}
    if delay is not None:
        body["error"]["details"] = [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}
        ]
    headers = {"retry-after": header} if header is not None else {}
    return httpx.Response(429, json=body, headers=headers)


def _then_ok(*failures):
    replies = iter(failures)

    def handler(request):
        reply = next(replies, None)
        return reply if reply is not None else _echo(request)

    return handler


def test_retry_hint_reads_the_header_and_the_retryinfo_body():
    assert gemini_retry_hint(_quota(header="12")) == 12.0
    assert gemini_retry_hint(_quota(delay="37s")) == 37.0
    assert gemini_retry_hint(_quota(delay="1.5s")) == 1.5
    assert gemini_retry_hint(_quota()) is None
    assert gemini_retry_hint(httpx.Response(429, text="not json")) is None


def test_the_wait_google_asks_for_is_honoured(gemini):
    seen = gemini(_then_ok(_quota(delay="37s")))
    assert len(EmbeddingService.get_embeddings(["x"])) == 1
    assert len(seen) == 2
    assert 37 <= sum(gemini.clock.sleeps) < 40


def test_without_a_hint_backoff_grows_exponentially(gemini):
    gemini(_then_ok(_quota(), _quota(), _quota()))
    EmbeddingService.get_embeddings(["x"])

    assert gemini.clock.sleeps == [2.0, 4.0, 8.0]


def test_running_out_of_budget_pauses_instead_of_failing(gemini, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_RETRY_BUDGET_SECONDS", 100)
    gemini(lambda _r: _quota(delay="30s"))

    with pytest.raises(EmbeddingRateLimited) as info:
        EmbeddingService.get_embeddings(["x"])
    assert info.value.retry_after == pytest.approx(30)
    assert sum(gemini.clock.sleeps) <= 100


def test_a_daily_quota_stops_at_once_with_the_delay_attached(gemini):
    seen = gemini(lambda _r: _quota(delay="3600s"))

    with pytest.raises(EmbeddingRateLimited) as info:
        EmbeddingService.get_embeddings(["x"])
    assert info.value.retry_after == 3600
    assert len(seen) == 1


def test_a_question_has_a_short_budget(gemini, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_QUERY_RETRY_BUDGET_SECONDS", 20)
    gemini(lambda _r: _quota(delay="15s"))

    with pytest.raises(EmbeddingRateLimited):
        EmbeddingService.get_embedding("q?")
    assert sum(gemini.clock.sleeps) <= 20


def test_network_errors_are_retried(gemini):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("reset")
        return _echo(request)

    gemini(handler)
    assert len(EmbeddingService.get_embeddings(["x"])) == 1
    assert calls["n"] == 2


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
