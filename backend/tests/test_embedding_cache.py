import pytest

from app.core.config import settings
from app.services import embedding
from app.services.embedding import EmbeddingService, query_cache


@pytest.fixture
def cache_on(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "mock")
    monkeypatch.setattr(settings, "EMBEDDING_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "EMBEDDING_CACHE_BACKEND", "memory")
    query_cache.clear()
    yield query_cache
    query_cache.clear()


def _count_provider_calls(monkeypatch) -> list:
    calls = []
    real = EmbeddingService._get_mock_embeddings.__func__

    def counting(cls, texts):
        calls.append(list(texts))
        return real(cls, texts)

    monkeypatch.setattr(EmbeddingService, "_get_mock_embeddings", classmethod(counting))
    return calls


class TestQueryCache:
    def test_a_repeated_question_skips_the_model(self, cache_on, monkeypatch):
        calls = _count_provider_calls(monkeypatch)
        first = EmbeddingService.get_embedding("What is the leave policy?")
        second = EmbeddingService.get_embedding("What is the leave policy?")
        assert first == second
        assert len(calls) == 1
        assert cache_on.hits == 1

    def test_it_is_off_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "mock")
        monkeypatch.setattr(settings, "EMBEDDING_CACHE_ENABLED", False)
        calls = _count_provider_calls(monkeypatch)
        EmbeddingService.get_embedding("q")
        EmbeddingService.get_embedding("q")
        assert len(calls) == 2

    def test_document_batches_are_never_cached(self, cache_on, monkeypatch):
        calls = _count_provider_calls(monkeypatch)
        EmbeddingService.get_embeddings(["chunk"])
        EmbeddingService.get_embeddings(["chunk"])
        assert len(calls) == 2

    def test_the_least_recently_used_entry_is_evicted(self, cache_on, monkeypatch):
        monkeypatch.setattr(settings, "EMBEDDING_CACHE_MAX_ENTRIES", 2)
        calls = _count_provider_calls(monkeypatch)
        for text in ("a", "b", "a", "c"):  # "b" is now least recently used
            EmbeddingService.get_embedding(text)
        assert len(calls) == 3
        EmbeddingService.get_embedding("a")   # still cached
        assert len(calls) == 3
        EmbeddingService.get_embedding("b")   # evicted -> recomputed
        assert len(calls) == 4

    def test_entries_expire(self, cache_on, monkeypatch):
        calls = _count_provider_calls(monkeypatch)
        clock = [1000.0]
        monkeypatch.setattr(embedding.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(settings, "EMBEDDING_CACHE_TTL_SECONDS", 60)
        EmbeddingService.get_embedding("q")
        clock[0] += 61
        EmbeddingService.get_embedding("q")
        assert len(calls) == 2

    def test_the_key_includes_the_model_so_switching_never_mixes_vectors(self):
        assert query_cache.key("model-a", "q") != query_cache.key("model-b", "q")
        assert "leave" not in query_cache.key("m", "leave policy")

    def test_a_broken_redis_falls_back_to_memory(self, cache_on, monkeypatch):
        class BrokenRedis:
            def get(self, key):
                raise ConnectionError("down")

            def set(self, *args, **kwargs):
                raise ConnectionError("down")

        monkeypatch.setattr(settings, "EMBEDDING_CACHE_BACKEND", "auto")
        monkeypatch.setattr(query_cache, "_redis", BrokenRedis())
        monkeypatch.setattr(query_cache, "_redis_down_until", 0.0)
        calls = _count_provider_calls(monkeypatch)
        EmbeddingService.get_embedding("q")
        EmbeddingService.get_embedding("q")
        assert len(calls) == 1  # the second one came from memory

    def test_redis_is_used_when_it_answers(self, cache_on, monkeypatch):
        class FakeRedis:
            def __init__(self):
                self.data = {}

            def get(self, key):
                return self.data.get(key)

            def set(self, key, value, ex=None):
                assert ex == settings.EMBEDDING_CACHE_TTL_SECONDS
                self.data[key] = value

        fake = FakeRedis()
        monkeypatch.setattr(settings, "EMBEDDING_CACHE_BACKEND", "auto")
        monkeypatch.setattr(query_cache, "_redis", fake)
        monkeypatch.setattr(query_cache, "_redis_down_until", 0.0)
        vector = EmbeddingService.get_embedding("shared question")
        assert len(fake.data) == 1
        query_cache._memory.clear()
        assert EmbeddingService.get_embedding("shared question") == vector


class TestMultilingual:
    def test_off_by_default(self):
        assert settings.EMBEDDING_MULTILINGUAL is False

    def test_switching_on_changes_the_local_model_and_its_recorded_name(self, monkeypatch):
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "local")
        monkeypatch.setattr(settings, "EMBEDDING_MULTILINGUAL", True)
        assert settings.local_embedding_model == "paraphrase-multilingual-MiniLM-L12-v2"
        assert EmbeddingService.current_model_name() == "paraphrase-multilingual-MiniLM-L12-v2"

    def test_off_uses_the_english_model(self, monkeypatch):
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "local")
        monkeypatch.setattr(settings, "EMBEDDING_MULTILINGUAL", False)
        assert EmbeddingService.current_model_name() == settings.LOCAL_EMBEDDING_MODEL
