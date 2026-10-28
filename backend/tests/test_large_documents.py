import asyncio
import csv
import datetime
import io
import json

import fitz
import httpx
import openpyxl
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.chunk import Chunk, ChunkEmbedding
from app.services import vision
from app.services.chunker import ChunkerService, StreamingChunker
from app.services.embedding import EmbeddingRateLimited, EmbeddingService
from app.services.extractor import DocumentExtractor, DocumentTooLarge
from app.services.ocr import OCRService
from app.services.pacing import RatePacer
from app.tasks import ingestion
from app.tasks.ingestion import async_process_document, recover_stuck_documents
from tests import test_ingestion_durability as durability


db = durability.db
local_store = durability.local_store


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


class TestPacer:
    def test_under_the_limit_nothing_waits(self):
        clock = FakeClock()
        pacer = RatePacer(clock=clock.now, sleep=clock.sleep)
        for _ in range(9):
            pacer.acquire(10, 100, rpm=90, tpm=10_000)
        assert clock.sleeps == []
        assert pacer.used() == (90, 900)

    def test_over_the_limit_waits_for_the_oldest_to_roll_off(self):
        clock = FakeClock()
        pacer = RatePacer(clock=clock.now, sleep=clock.sleep)
        pacer.acquire(50, 0, rpm=90, tpm=0)
        clock.t = 20
        pacer.acquire(30, 0, rpm=90, tpm=0)
        clock.t = 30
        waited = pacer.acquire(20, 0, rpm=90, tpm=0)  # 100 > 90: wait for the first
        assert waited == pytest.approx(30.05)
        assert clock.t == pytest.approx(60.05)

    def test_the_token_budget_counts_too(self):
        clock = FakeClock()
        pacer = RatePacer(clock=clock.now, sleep=clock.sleep)
        pacer.acquire(1, 20_000, rpm=100, tpm=25_000)
        pacer.acquire(1, 10_000, rpm=100, tpm=25_000)
        assert clock.sleeps and clock.sleeps[0] == pytest.approx(60.05)

    def test_an_oversized_call_goes_once_the_window_is_empty(self):
        clock = FakeClock()
        pacer = RatePacer(clock=clock.now, sleep=clock.sleep)
        pacer.acquire(1, 0, rpm=10, tpm=0)
        pacer.acquire(50, 0, rpm=10, tpm=0)  # bigger than a minute: no deadlock
        assert sum(clock.sleeps) == pytest.approx(60.05)

    def test_a_cooldown_holds_every_caller(self):
        clock = FakeClock()
        pacer = RatePacer(clock=clock.now, sleep=clock.sleep)
        pacer.cool_down(37)
        pacer.acquire(1, 1, rpm=100, tpm=100)
        assert clock.sleeps == [37]

    def test_non_blocking_records_without_waiting(self):
        clock = FakeClock()
        pacer = RatePacer(clock=clock.now, sleep=clock.sleep)
        pacer.acquire(10, 0, rpm=10, tpm=0)
        pacer.acquire(10, 0, rpm=10, tpm=0, block=False)
        assert clock.sleeps == [] and pacer.used() == (20, 0)

    def test_max_wait_raises(self):
        clock = FakeClock()
        pacer = RatePacer(clock=clock.now, sleep=clock.sleep)
        pacer.acquire(10, 0, rpm=10, tpm=0)
        with pytest.raises(TimeoutError):
            pacer.acquire(1, 0, rpm=10, tpm=0, max_wait=5)


def _pdf(path, pages: int, text: str = "Page {n}: refunds are available within thirty days.") -> str:
    document = fitz.open()
    for n in range(1, pages + 1):
        page = document.new_page()
        page.insert_text((72, 72), text.format(n=n))
    document.save(path)
    document.close()
    return str(path)


class TestStreamingExtraction:
    def test_a_pdf_streams_one_page_at_a_time(self, tmp_path):
        path = _pdf(tmp_path / "a.pdf", 5)
        info: dict = {}
        segments = list(DocumentExtractor.iter_segments(path, "pdf", info))

        assert [s.page_number for s in segments] == [1, 2, 3, 4, 5]
        assert "Page 3" in segments[2].text
        assert info["page_count"] == 5
        assert not any(s.ocr for s in segments)

    def test_it_is_a_generator_not_a_list(self, tmp_path):
        path = _pdf(tmp_path / "a.pdf", 3)
        segments = DocumentExtractor.iter_segments(path, "pdf", {})
        first = next(segments)
        assert first.page_number == 1
        segments.close()  # closes the PDF; the rest is never read

    def test_too_many_pages_is_a_clear_permanent_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_MAX_PAGES", 3)
        path = _pdf(tmp_path / "a.pdf", 4)
        with pytest.raises(DocumentTooLarge, match="4 pages; the limit is 3"):
            list(DocumentExtractor.iter_segments(path, "pdf", {}))
        assert not ingestion.is_transient(DocumentTooLarge("x"))

    def test_a_blank_page_is_not_sent_to_ocr(self, tmp_path, monkeypatch):
        document = fitz.open()
        document.new_page()
        path = str(tmp_path / "blank.pdf")
        document.save(path)
        document.close()
        called = []
        monkeypatch.setattr(OCRService, "ocr_page", classmethod(lambda cls, page: called.append(1) or ""))
        list(DocumentExtractor.iter_segments(path, "pdf", {}))
        assert called == []

    def test_a_csv_streams_in_row_batches_with_the_header_repeated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_ROWS_PER_SEGMENT", 100)
        path = tmp_path / "big.csv"
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Region", "Budget", "Actual"])
            for n in range(250):
                writer.writerow([f"R{n}", n, n * 2])
        info: dict = {}
        segments = list(DocumentExtractor.iter_segments(str(path), "csv", info))

        assert len(segments) == 3
        assert info["row_count"] == 251
        assert segments[1].text.splitlines()[0] == "Region , Budget , Actual"
        assert segments[1].meta["rows"] == "101-200"

    def test_an_xlsx_streams_sheet_by_sheet(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_ROWS_PER_SEGMENT", 50)
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Q3"
        sheet.append(["Item", "Cost"])
        for n in range(120):
            sheet.append([f"item {n}", n])
        workbook.create_sheet("Notes").append(["hello"])
        path = str(tmp_path / "book.xlsx")
        workbook.save(path)

        segments = list(DocumentExtractor.iter_segments(path, "xlsx", {}))
        assert [s.meta["sheet"] for s in segments] == ["Q3", "Q3", "Q3", "Notes"]
        assert "Sheet 'Q3' (rows 51-100):" in segments[1].text
        assert "Item | Cost" in segments[1].text

    def test_a_long_text_file_is_sliced(self, tmp_path, monkeypatch):
        monkeypatch.setattr(DocumentExtractor, "_TEXT_SEGMENT_BYTES", 1000)
        path = tmp_path / "long.txt"
        path.write_text("a line of text\n" * 500, encoding="utf-8")
        segments = list(DocumentExtractor.iter_segments(str(path), "txt", {}))
        assert len(segments) > 5
        assert "".join(s.text for s in segments) == path.read_text(encoding="utf-8")

    def test_other_formats_come_whole(self, tmp_path):
        path = tmp_path / "page.html"
        path.write_text("<html><body><p>Hello refunds</p></body></html>", encoding="utf-8")
        (segment,) = DocumentExtractor.iter_segments(str(path), "html", {})
        assert segment.page_number is None and "Hello refunds" in segment.text


class TestStreamingChunker:
    def test_numbering_continues_across_segments(self):
        chunker = StreamingChunker("fixed_size", 50, 5)
        first = chunker.feed("word " * 120, 1)
        second = chunker.feed("other " * 120, 2)
        indexes = [c["chunk_index"] for c in first + second]
        assert indexes == list(range(len(indexes)))
        assert {c["page_number"] for c in second} == {2}

    def test_it_matches_whole_document_chunking_per_page(self):
        text = "Sentence one is here. " * 200
        streamed = StreamingChunker("sentence", 100, 10).feed(text, 1)
        whole = ChunkerService.chunk_document(text, strategy="sentence", chunk_size=100, chunk_overlap=10)
        assert [c["content"] for c in streamed] == [c["content"] for c in whole]

    def test_segment_meta_is_copied_onto_chunks(self):
        chunks = StreamingChunker("fixed_size", 500, 50).feed("x y z", 1, {"sheet": "Q3", "rows": "1-200"})
        assert chunks[0]["meta_data"]["sheet"] == "Q3"

    def test_a_whole_document_segment_keeps_its_own_page_markers(self):
        chunker = StreamingChunker("fixed_size", 500, 50)
        chunks = chunker.feed("--- PAGE 1 (OCR) ---\nalpha\n--- PAGE 2 (OCR) ---\nbeta", None)
        assert [c["page_number"] for c in chunks] == [1, 2]


class TestOcrPageSize:
    def test_an_ordinary_page_keeps_the_configured_dpi(self):
        page = fitz.open().new_page(width=595, height=842)  # A4
        assert OCRService.page_dpi(page) == settings.OCR_DPI

    def test_a_poster_is_rasterised_smaller(self, monkeypatch):
        monkeypatch.setattr(settings, "OCR_MAX_PIXELS", 12_000_000)
        page = fitz.open().new_page(width=2384, height=3370)  # A0
        dpi = OCRService.page_dpi(page)
        assert 72 <= dpi < settings.OCR_DPI
        pixels = (2384 / 72 * dpi) * (3370 / 72 * dpi)
        assert pixels <= 12_000_000


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format="PNG")
    return out.getvalue()


class TestVision:
    def test_off_by_default_and_costs_nothing(self):
        assert settings.VISION_PROVIDER == "off"
        assert vision.caption_image(_png()) is None
        assert vision.caption_block(None) == ""

    def test_groq_caption(self, monkeypatch):
        monkeypatch.setattr(settings, "VISION_PROVIDER", "groq")
        monkeypatch.setattr(settings, "GROQ_API_KEY", "k")
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": " An org  chart. "}}]})

        monkeypatch.setattr(vision, "_transport", httpx.MockTransport(handler))
        monkeypatch.setattr(vision, "vision_pacer", RatePacer())
        assert vision.caption_image(_png()) == "An org chart."
        image_part = seen[0]["messages"][0]["content"][1]
        assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert "[Image description] An org chart." in vision.caption_block("An org chart.")

    def test_a_failure_never_breaks_the_document(self, monkeypatch):
        monkeypatch.setattr(settings, "VISION_PROVIDER", "gemini")
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "k")
        monkeypatch.setattr(vision, "_transport", httpx.MockTransport(lambda r: httpx.Response(500)))
        monkeypatch.setattr(vision, "vision_pacer", RatePacer())
        assert vision.caption_image(_png()) is None

    def test_no_key_means_no_caption(self, monkeypatch):
        monkeypatch.setattr(settings, "VISION_PROVIDER", "groq")
        monkeypatch.setattr(settings, "GROQ_API_KEY", "")
        monkeypatch.setattr(settings, "VISION_API_KEY", "")
        assert vision.caption_image(_png()) is None


LONG_TEXT = ("Refunds are available within thirty days of purchase. " * 40 + "\n\n") * 12


async def _embedded(db, document_id) -> int:
    async with db.factory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(ChunkEmbedding)
            .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
            .where(Chunk.document_id == document_id)
        )
        return result.scalar_one()


class FlakyEmbeddings:
    """Real mock vectors for the first `allow` calls, then a rate limit."""

    def __init__(self, allow: int):
        self.allow = allow
        self.calls = 0
        self.texts = 0
        self.real = EmbeddingService.get_embeddings

    def __call__(self, texts):
        self.calls += 1
        if self.calls > self.allow:
            raise EmbeddingRateLimited("HTTP 429", retry_after=30)
        self.texts += len(texts)
        return EmbeddingService._get_mock_embeddings(texts)


class TestResume:
    @pytest.fixture(autouse=True)
    def small_batches(self, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_EMBED_BATCH_SIZE", 3)
        monkeypatch.setattr(settings, "DEFAULT_CHUNK_SIZE", 60)
        monkeypatch.setattr(settings, "DEFAULT_CHUNK_OVERLAP", 5)

    async def test_a_rate_limit_pauses_and_the_resume_skips_finished_chunks(self, db, monkeypatch):
        document_id = await db.document(LONG_TEXT.encode())
        flaky = FlakyEmbeddings(allow=2)
        monkeypatch.setattr(EmbeddingService, "get_embeddings", staticmethod(flaky))

        assert await async_process_document(str(document_id)) == "paused"
        paused = await db.get(document_id)
        total = await db.chunk_count(document_id)
        assert total > 6
        assert paused.status == "pending"
        assert paused.resume_after is not None
        assert paused.meta_data["progress"]["stage"] == "paused"
        assert paused.meta_data["progress"]["embedded"] == 6
        assert await _embedded(db, document_id) == 6
        assert "Waiting for the embedding rate limit" in paused.error_message

        healthy = FlakyEmbeddings(allow=10_000)
        monkeypatch.setattr(EmbeddingService, "get_embeddings", staticmethod(healthy))
        assert await async_process_document(str(document_id)) == "completed"

        done = await db.get(document_id)
        assert done.status == "completed" and done.resume_after is None
        assert healthy.texts == total - 6
        assert await _embedded(db, document_id) == total
        assert await db.chunk_count(document_id) == total
        assert done.meta_data["progress"] == {"stage": "completed", "embedded": total, "total": total}
        assert "error" not in done.meta_data

    async def test_a_pause_that_made_progress_resets_attempts(self, db, monkeypatch):
        document_id = await db.document(LONG_TEXT.encode())
        monkeypatch.setattr(EmbeddingService, "get_embeddings", staticmethod(FlakyEmbeddings(allow=1)))
        await async_process_document(str(document_id))
        assert (await db.get(document_id)).attempts == 0

    async def test_pauses_without_progress_back_off_then_fail(self, db, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_PAUSE_SECONDS", 100)
        monkeypatch.setattr(settings, "INGESTION_MAX_IDLE_PAUSES", 2)
        document_id = await db.document(LONG_TEXT.encode())
        monkeypatch.setattr(EmbeddingService, "get_embeddings", staticmethod(FlakyEmbeddings(allow=0)))

        delays = []
        for _ in range(2):
            before = datetime.datetime.now(datetime.timezone.utc)
            assert await async_process_document(str(document_id)) == "paused"
            resume_after = (await db.get(document_id)).resume_after
            if resume_after.tzinfo is None:
                resume_after = resume_after.replace(tzinfo=datetime.timezone.utc)
            delays.append((resume_after - before).total_seconds())
        assert delays[1] > delays[0] * 1.5  # doubled

        assert await async_process_document(str(document_id)) == "failed"
        failed = await db.get(document_id)
        assert failed.status == "failed"
        assert "Re-index resumes" in failed.error_message

    async def test_too_many_chunks_fails_permanently(self, db, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_MAX_CHUNKS", 3)
        document_id = await db.document(LONG_TEXT.encode())
        assert await async_process_document(str(document_id)) == "failed"
        failed = await db.get(document_id)
        assert "INGESTION_MAX_CHUNKS" in failed.error_message

    async def test_a_pdf_is_ingested_page_by_page_with_citations(self, db, tmp_path):
        content = open(_pdf(tmp_path / "doc.pdf", 25), "rb").read()
        document_id = await db.document(content, extension="pdf")
        assert await async_process_document(str(document_id)) == "completed"

        async with db.factory() as session:
            pages = (
                await session.execute(select(Chunk.page_number).where(Chunk.document_id == document_id))
            ).scalars().all()
        assert sorted(set(pages)) == list(range(1, 26))
        done = await db.get(document_id)
        assert done.meta_data["page_count"] == 25


class TestRecoveryOfPausedDocuments:
    async def test_a_paused_document_is_resumed_only_once_due(self, db):
        now = datetime.datetime.now(datetime.timezone.utc)
        later = await db.document(status="pending", resume_after=now + datetime.timedelta(minutes=5))
        due = await db.document(status="pending", resume_after=now - datetime.timedelta(seconds=1))

        result = await recover_stuck_documents()
        assert str(due) in result["requeued"] and str(due) in result["resumed"]
        assert str(later) not in result["requeued"]
        assert (await db.get(due)).resume_after is None
        assert (await db.get(later)).resume_after is not None


class TestInlineQueue:
    async def test_documents_run_one_at_a_time_and_are_not_queued_twice(self, monkeypatch):
        monkeypatch.setattr(settings, "INGESTION_INLINE_CONCURRENCY", 1)
        running = {"now": 0, "max": 0}
        processed = []

        async def fake_run(document_id, *, force=False):
            running["now"] += 1
            running["max"] = max(running["max"], running["now"])
            await asyncio.sleep(0.01)
            processed.append(document_id)
            running["now"] -= 1
            return "completed"

        monkeypatch.setattr(ingestion, "run_inline_ingestion", fake_run)
        queue = ingestion.InlineQueue()
        assert queue.submit("a") and queue.submit("b") and queue.submit("c")
        assert queue.submit("a") is False  # already queued
        await queue.join()
        await queue.stop()

        assert processed == ["a", "b", "c"]
        assert running["max"] == 1
        assert queue.pending() == 0
