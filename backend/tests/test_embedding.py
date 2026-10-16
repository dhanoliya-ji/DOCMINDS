"""
test_embedding.py
=================
WHAT THIS FILE TESTS
--------------------
`services/embedding.py`, exercised through the **mock** provider.

WHY THE MOCK PROVIDER IS WORTH TESTING
--------------------------------------
It is not a stub to be tolerated -- it is load-bearing infrastructure. Every
other test that wants to put a vector in a database depends on it producing
the same vector for the same text, every run, on every machine.

If that determinism ever broke, those tests would not fail cleanly. They would
go *flaky*: passing locally, failing in CI, passing again on a re-run. So the
guarantee is asserted here directly, where a break is unambiguous.

The contracts checked below -- correct dimension, order preserved, unit
length -- are also exactly the contracts the real providers must meet, and the
database enforces the first of them at insert time.

TIER 1: no database, no network, no API key.
"""

import pytest

from app.core.config import settings
from app.services.embedding import EmbeddingService


@pytest.fixture(autouse=True)
def force_mock_provider(monkeypatch):
    """
    Pin the provider to `mock` for every test in this file.

    `monkeypatch` restores the original value afterwards, so this cannot leak
    into another test file -- which matters, because the setting is global.
    """
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "mock")


DIMENSION = settings.EMBEDDING_DIMENSION


# ======================================================================
# The contract every provider must meet
# ======================================================================


class TestVectorShape:
    def test_a_vector_has_the_configured_dimension(self):
        """
        The database column is declared with a fixed width, so a vector of the
        wrong length fails at insert with "expected N dimensions, not M".
        """
        assert len(EmbeddingService.get_embedding("some text")) == DIMENSION

    def test_every_vector_in_a_batch_has_that_dimension(self):
        vectors = EmbeddingService.get_embeddings(["one", "two", "three"])
        assert all(len(v) == DIMENSION for v in vectors)

    def test_a_vector_is_a_list_of_floats(self):
        vector = EmbeddingService.get_embedding("some text")
        assert isinstance(vector, list)
        assert all(isinstance(value, float) for value in vector)

    def test_vectors_are_unit_length(self):
        """
        Normalised to length 1, matching the real providers. This is what keeps
        cosine similarity on a comparable 0..1 scale, so RETRIEVAL_MIN_SCORE
        means the same thing whichever provider is configured.
        """
        for text in ("short", "a considerably longer piece of text here", "x"):
            vector = EmbeddingService.get_embedding(text)
            magnitude = sum(value * value for value in vector) ** 0.5
            assert magnitude == pytest.approx(1.0, abs=1e-9)


class TestBatching:
    def test_the_batch_length_matches_the_input(self):
        texts = [f"text number {i}" for i in range(7)]
        assert len(EmbeddingService.get_embeddings(texts)) == 7

    def test_order_is_preserved(self):
        """
        The ingestion task pairs vectors back to chunks by position alone. If
        the order were not guaranteed, chunks would be stored with each other's
        vectors -- and every citation would point at the wrong passage, with
        nothing crashing to reveal it.
        """
        texts = ["alpha", "bravo", "charlie"]
        batched = EmbeddingService.get_embeddings(texts)

        for text, vector in zip(texts, batched, strict=True):
            assert vector == EmbeddingService.get_embedding(text)

    def test_an_empty_batch_returns_an_empty_list(self):
        """Guarded explicitly, because some providers reject a zero-length batch."""
        assert EmbeddingService.get_embeddings([]) == []


# ======================================================================
# Determinism -- the property the mock exists to provide
# ======================================================================


class TestDeterminism:
    def test_the_same_text_always_gives_the_same_vector(self):
        first = EmbeddingService.get_embedding("How much annual leave do I get?")
        second = EmbeddingService.get_embedding("How much annual leave do I get?")

        assert first == second

    def test_different_texts_give_different_vectors(self):
        """
        Otherwise every chunk would sit at the same point and search would
        return an arbitrary result -- while looking like it worked.
        """
        assert EmbeddingService.get_embedding("alpha") != EmbeddingService.get_embedding("bravo")

    def test_texts_differing_by_one_character_differ(self):
        assert EmbeddingService.get_embedding("report") != EmbeddingService.get_embedding("reports")

    def test_it_does_not_disturb_the_global_random_state(self):
        """
        The mock seeds a *private* Random instance. Seeding the global one
        instead would make anything else using `random` suddenly reproducible
        -- a genuinely confusing action at a distance.
        """
        import random

        random.seed(12345)
        expected = [random.random() for _ in range(3)]

        random.seed(12345)
        EmbeddingService.get_embedding("this must not disturb anything")
        actual = [random.random() for _ in range(3)]

        assert actual == expected


# ======================================================================
# Configuration
# ======================================================================


class TestProviderSelection:
    def test_the_provider_name_is_case_insensitive(self, monkeypatch):
        """A capitalisation typo in .env should not break the app."""
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "MOCK")
        assert len(EmbeddingService.get_embedding("text")) == DIMENSION

    def test_an_unknown_provider_falls_back_to_mock(self, monkeypatch):
        """
        A deliberate choice: warn loudly, but still start. A hard crash on boot
        over a misspelled setting is harder to diagnose than a running app
        producing obviously meaningless search results.
        """
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "not-a-real-provider")

        vector = EmbeddingService.get_embedding("text")

        assert len(vector) == DIMENSION
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "mock")
        assert vector == EmbeddingService.get_embedding("text")

    def test_current_model_name_is_reported(self):
        """
        Stored beside every vector, so a half-migrated corpus is diagnosable
        after an embedding-model change.
        """
        name = EmbeddingService.current_model_name()
        assert isinstance(name, str) and name


class TestEdgeCases:
    @pytest.mark.parametrize("text", ["", " ", "\n", "x", "🔑", "a" * 5000])
    def test_unusual_text_still_produces_a_valid_vector(self, text):
        vector = EmbeddingService.get_embedding(text)
        assert len(vector) == DIMENSION
