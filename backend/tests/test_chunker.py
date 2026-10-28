import pytest

from app.services.chunker import ChunkerService


PAGE_BREAK = "\n--- PAGE BREAK ---\n"


def make_text(word: str, count: int) -> str:
    """Build a long run of one repeated word, for size-based tests."""
    return " ".join([word] * count)


class TestCountTokens:
    """Tokens are the unit everything else is measured in."""

    def test_empty_text_is_zero_tokens(self):
        assert ChunkerService.count_tokens("") == 0

    def test_counts_grow_with_text(self):
        short = ChunkerService.count_tokens("hello")
        longer = ChunkerService.count_tokens("hello there, how are you today?")
        assert 0 < short < longer

    def test_tokens_are_not_characters(self):
        """The whole reason the chunker works in token space."""
        text = "unbelievable"
        assert ChunkerService.count_tokens(text) < len(text)

    def test_same_text_always_counts_the_same(self):
        """Deterministic, so chunk sizes are reproducible across runs."""
        text = "The quick brown fox jumps over the lazy dog."
        assert ChunkerService.count_tokens(text) == ChunkerService.count_tokens(text)


class TestEmptyAndTrivialInput:
    """Degenerate inputs must return empty, not crash and not return junk."""

    @pytest.mark.parametrize("text", ["", "   ", "\n\n\n", "\t  \n "])
    def test_blank_text_produces_no_chunks(self, text):
        assert ChunkerService.chunk_document(text) == []

    def test_page_markers_with_no_content_produce_no_chunks(self):
        """A document of nothing but page breaks has nothing to embed."""
        text = PAGE_BREAK + PAGE_BREAK + PAGE_BREAK
        assert ChunkerService.chunk_document(text) == []

    def test_short_text_becomes_exactly_one_chunk(self):
        chunks = ChunkerService.chunk_document("A short sentence.", chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0]["content"] == "A short sentence."


class TestChunkShape:
    """Every chunk must carry the four keys the ingestion task writes to a row."""

    def test_each_chunk_has_the_required_keys(self):
        chunks = ChunkerService.chunk_document(make_text("word", 300), chunk_size=50)
        assert chunks
        for chunk in chunks:
            assert set(chunk) == {"content", "chunk_index", "page_number", "meta_data"}
            assert isinstance(chunk["content"], str)
            assert isinstance(chunk["chunk_index"], int)
            assert isinstance(chunk["page_number"], int)

    def test_metadata_reports_real_measurements(self):
        chunks = ChunkerService.chunk_document(make_text("word", 300), chunk_size=50)
        for chunk in chunks:
            meta = chunk["meta_data"]
            assert meta["character_count"] == len(chunk["content"])
            assert meta["token_count"] == ChunkerService.count_tokens(chunk["content"])

    def test_no_chunk_is_empty(self):
        """An empty chunk would embed to a meaningless vector and pollute search."""
        chunks = ChunkerService.chunk_document(make_text("word", 500), chunk_size=40)
        assert all(chunk["content"].strip() for chunk in chunks)


class TestChunkIndex:
    """`chunk_index` is the position of a chunk within the WHOLE document."""

    def test_index_starts_at_zero_and_increments_by_one(self):
        chunks = ChunkerService.chunk_document(make_text("word", 400), chunk_size=50)
        assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))

    def test_index_keeps_counting_across_pages(self):
        """The bookkeeping detail that is easy to get wrong."""
        page = make_text("word", 200)
        text = page + PAGE_BREAK + page + PAGE_BREAK + page

        chunks = ChunkerService.chunk_document(text, chunk_size=50)
        indexes = [c["chunk_index"] for c in chunks]

        assert indexes == list(range(len(chunks)))
        assert len(set(indexes)) == len(indexes), "indexes must be unique"
        assert max(indexes) > 3


class TestPageAwareness:
    def test_pages_are_numbered_from_one(self):
        """Humans number pages from 1, so the first page is 1 and not 0."""
        chunks = ChunkerService.chunk_document("First page.")
        assert chunks[0]["page_number"] == 1

    def test_each_page_gets_its_own_number(self):
        text = "Alpha content here." + PAGE_BREAK + "Beta content here." + PAGE_BREAK + "Gamma content here."

        chunks = ChunkerService.chunk_document(text)
        pages = [c["page_number"] for c in chunks]

        assert pages == [1, 2, 3]

    def test_content_stays_on_the_right_page(self):
        """The property citations actually depend on."""
        text = "Alpha unique." + PAGE_BREAK + "Beta unique." + PAGE_BREAK + "Gamma unique."

        by_page = {c["page_number"]: c["content"] for c in ChunkerService.chunk_document(text)}

        assert "Alpha" in by_page[1]
        assert "Beta" in by_page[2]
        assert "Gamma" in by_page[3]

    def test_no_chunk_spans_a_page_boundary(self):
        """Pages are chunked independently, so no chunk may contain text from two"""
        text = make_text("alpha", 300) + PAGE_BREAK + make_text("beta", 300)

        for chunk in ChunkerService.chunk_document(text, chunk_size=100):
            content = chunk["content"]
            assert not ("alpha" in content and "beta" in content)

    def test_the_ocr_page_marker_is_also_recognised(self):
        """normal extraction and "--- PAGE 3 (OCR) ---" from the OCR path. A"""
        text = "Scanned first page.\n--- PAGE 2 (OCR) ---\nScanned second page."

        chunks = ChunkerService.chunk_document(text)

        assert [c["page_number"] for c in chunks] == [1, 2]
        assert "Scanned first" in chunks[0]["content"]
        assert "Scanned second" in chunks[1]["content"]


class TestFixedSizeChunking:
    def test_chunks_respect_the_size_limit(self):
        chunks = ChunkerService.chunk_document(
            make_text("word", 2000), strategy="fixed_size", chunk_size=100, chunk_overlap=10
        )

        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk["meta_data"]["token_count"] <= 100

    def test_a_long_document_is_split_into_many_chunks(self):
        long_text = make_text("word", 2000)

        one_chunk = ChunkerService.chunk_document(long_text, chunk_size=100000)
        many = ChunkerService.chunk_document(long_text, chunk_size=100)

        assert len(one_chunk) == 1
        assert len(many) > 10

    def test_overlap_actually_repeats_text(self):
        """The reason overlap exists at all. Consecutive chunks must share some"""
        chunks = ChunkerService.chunk_document(
            make_text("alpha bravo charlie delta", 200),
            strategy="fixed_size",
            chunk_size=100,
            chunk_overlap=30,
        )

        assert len(chunks) >= 2

        first_tokens = ChunkerService._encoder.encode(chunks[0]["content"])
        second_tokens = ChunkerService._encoder.encode(chunks[1]["content"])

        assert first_tokens[-30:] == second_tokens[:30]

    def test_more_overlap_produces_more_chunks(self):
        """Overlap advances the window less far, so the same text needs more."""
        text = make_text("word", 1000)

        few = ChunkerService.chunk_document(text, chunk_size=100, chunk_overlap=0)
        many = ChunkerService.chunk_document(text, chunk_size=100, chunk_overlap=50)

        assert len(many) > len(few)

    def test_zero_overlap_repeats_nothing(self):
        chunks = ChunkerService.chunk_document(
            make_text("word", 500), chunk_size=100, chunk_overlap=0
        )

        rebuilt = "".join(c["content"] for c in chunks)
        original_token_count = ChunkerService.count_tokens(make_text("word", 500))

        assert ChunkerService.count_tokens(rebuilt) == original_token_count

    def test_overlap_larger_than_chunk_size_does_not_hang(self):
        """A nonsensical configuration makes `step` zero or negative, which would"""
        chunks = ChunkerService.chunk_document(
            make_text("word", 300), chunk_size=50, chunk_overlap=500
        )

        assert len(chunks) > 1
        assert all(c["meta_data"]["token_count"] <= 50 for c in chunks)

    def test_no_trailing_chunk_of_pure_repetition(self):
        """With overlap, a naive loop emits a final window made entirely of text"""
        text = " ".join(f"w{i}" for i in range(420))

        chunks = ChunkerService.chunk_document(text, chunk_size=100, chunk_overlap=50)

        last_tokens = set(ChunkerService._encoder.encode(chunks[-1]["content"]))
        previous_tokens = set(ChunkerService._encoder.encode(chunks[-2]["content"]))

        assert last_tokens - previous_tokens, (
            "the final chunk is entirely repeated from the previous one"
        )


class TestSentenceChunking:
    def test_produces_chunks(self):
        text = " ".join(f"This is sentence number {i}." for i in range(200))
        chunks = ChunkerService.chunk_document(text, strategy="sentence", chunk_size=100)

        assert len(chunks) > 1
        assert all(c["content"].strip() for c in chunks)

    def test_keeps_short_sentences_whole(self):
        """The point of the strategy: do not cut mid-thought."""
        text = "The cat sat on the mat. The dog barked loudly. Birds sang."
        chunks = ChunkerService.chunk_document(text, strategy="sentence", chunk_size=500)

        assert len(chunks) == 1
        for fragment in ("The cat sat", "The dog barked", "Birds sang"):
            assert fragment in chunks[0]["content"]

    def test_respects_the_token_budget(self):
        text = " ".join(f"Sentence {i} has a few words in it." for i in range(300))
        chunks = ChunkerService.chunk_document(text, strategy="sentence", chunk_size=80)

        assert len(chunks) > 1
        assert sum(1 for c in chunks if c["meta_data"]["token_count"] > 160) == 0


class TestMarkdownChunking:
    def test_splits_on_headers(self):
        text = (
            "# Introduction\nIntro body text.\n\n"
            "# Methods\nMethods body text.\n\n"
            "# Results\nResults body text.\n"
        )

        chunks = ChunkerService.chunk_document(text, strategy="markdown")

        assert len(chunks) >= 2
        joined = " ".join(c["content"] for c in chunks)
        assert "Introduction" in joined and "Results" in joined

    def test_works_on_the_whole_document_not_page_by_page(self):
        """The deliberate exception. A section under one header can legitimately"""
        text = "# Section One\nStart of the section." + PAGE_BREAK + "End of the same section."

        chunks = ChunkerService.chunk_document(text, strategy="markdown")

        joined = " ".join(c["content"] for c in chunks)
        assert "Start of the section" in joined
        assert "End of the same section" in joined


class TestStrategySelection:
    def test_an_unknown_strategy_raises(self):
        """Fail loudly on a typo rather than silently falling back to a default"""
        with pytest.raises(ValueError, match="Unsupported chunking strategy"):
            ChunkerService.chunk_document("Some text.", strategy="fixed-size")

    @pytest.mark.parametrize("strategy", ["FIXED_SIZE", "Sentence", "MarkDown"])
    def test_strategy_names_are_case_insensitive(self, strategy):
        assert ChunkerService.chunk_document("Some text here.", strategy=strategy) != []
