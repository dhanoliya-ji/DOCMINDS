"""
test_retrieval_db.py
====================
WHAT THIS FILE TESTS
--------------------
`services/retrieval.py` against a real PostgreSQL with pgvector -- the search
that everything else in the product depends on, and the multi-tenant filter
that keeps one organisation's documents invisible to another.

TIER 2. These skip cleanly when no database is reachable. See conftest.py for
why SQLite is not an acceptable substitute here: the query under test uses the
`vector` type and the `<=>` distance operator, so a SQLite run would be a
different test that passes while the real query is broken.

    docker compose up -d && alembic upgrade head

THE TEST THAT MATTERS MOST
--------------------------
`TestTenantIsolation`. Everything else in this file is about search quality --
worth having, but a bug there produces bad answers. A bug in the tenant filter
produces one company reading another company's documents, and nothing about
the system's behaviour would reveal it: the results would look perfectly
plausible.

It is asserted here from the outside, against real rows, because that is the
only way to know the filter is in the SQL rather than merely in the docstring.

WHY THE MOCK EMBEDDING PROVIDER IS USED
---------------------------------------
Its vectors are meaningless but DETERMINISTIC -- the same text always gives the
same vector. That is enough to test the plumbing: that a chunk's own text
retrieves that chunk, that filters narrow the results, and that another
tenant's rows never appear. Testing whether the embeddings are semantically
*good* is a different question, and not one a unit test can answer.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.chunk import Chunk, ChunkEmbedding
from app.models.document import Document
from app.models.organization import Organization
from app.models.project import Project
from app.services.embedding import EmbeddingService
from app.services.retrieval import RetrievalService
from tests.conftest import database_available

# Applied to every test in the module. Collection still happens; execution is
# skipped with a reason naming what was wrong.
_available, _reason = database_available()
pytestmark = pytest.mark.skipif(not _available, reason=f"Tier 2: {_reason or 'no database'}")


# ======================================================================
# Building a small, isolated corpus
# ======================================================================


class Corpus:
    """
    One organisation, one project, one document and its chunks.

    Bundled into an object so a test can say `alpha.org_id` rather than
    unpacking a five-element tuple, and so cleanup can delete exactly what it
    created.
    """

    def __init__(self, org, project, document, chunks):
        self.org = org
        self.project = project
        self.document = document
        self.chunks = chunks

    @property
    def org_id(self):
        return self.org.id

    @property
    def project_id(self):
        return self.project.id

    @property
    def document_id(self):
        return self.document.id


async def build_corpus(db: AsyncSession, name: str, texts: list[str], status="completed") -> Corpus:
    """
    Insert a complete ownership chain plus embedded chunks.

    Every level is created because the retrieval query JOINS all of them --
    chunk -> document -> project -> organisation. A chunk with no project
    above it is not merely untidy; it is invisible to search.
    """
    org = Organization(name=f"Org {name}")
    db.add(org)
    await db.flush()

    project = Project(name=f"Project {name}", org_id=org.id)
    db.add(project)
    await db.flush()

    document = Document(
        filename=f"{name}.pdf",
        file_type="pdf",
        file_size=1024,
        file_path=f"/tmp/{name}.pdf",
        status=status,
        # Unique per document: the schema enforces uniqueness on
        # (project_id, duplicate_hash), and reusing one would collide.
        duplicate_hash=uuid.uuid4().hex,
        project_id=project.id,
        meta_data={},
    )
    db.add(document)
    await db.flush()

    chunks = []
    vectors = EmbeddingService.get_embeddings(texts)

    for index, (text, vector) in enumerate(zip(texts, vectors, strict=True)):
        chunk = Chunk(
            document_id=document.id,
            content=text,
            chunk_index=index,
            page_number=index + 1,
            meta_data={},
        )
        db.add(chunk)
        await db.flush()

        db.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                embedding=vector,
                model_name=EmbeddingService.current_model_name(),
            )
        )
        chunks.append(chunk)

    await db.commit()
    return Corpus(org, project, document, chunks)


@pytest_asyncio.fixture
async def db(use_mock_providers):
    """
    A session for one test, with every row it created removed afterwards.

    Deleting the organisations is enough: every level below cascades, which is
    both convenient here and a genuine check that the CASCADE declarations in
    models/ are what they claim to be.
    """
    created_org_ids: list[uuid.UUID] = []

    async with SessionLocal() as session:
        session.info["created_org_ids"] = created_org_ids
        try:
            yield session
        finally:
            await session.rollback()
            for org_id in created_org_ids:
                await session.execute(delete(Organization).where(Organization.id == org_id))
            await session.commit()


async def make(db: AsyncSession, name: str, texts: list[str], **kwargs) -> Corpus:
    """Build a corpus and register it for cleanup."""
    corpus = await build_corpus(db, name, texts, **kwargs)
    db.info["created_org_ids"].append(corpus.org_id)
    return corpus


# ======================================================================
# THE ONE THAT MATTERS: multi-tenant isolation
# ======================================================================


class TestTenantIsolation:
    """
    `org_id` is the security boundary, and it is enforced inside the query --
    the search joins chunk -> document -> project and filters on
    `Project.org_id`, so another tenant's chunk is never a candidate.

    These tests are the difference between that being true and it being a
    comment that used to be true.
    """

    async def test_a_search_never_returns_another_organisations_chunks(self, db):
        secret = "The acquisition price agreed with Northwind is 4.2 million."

        alpha = await make(db, "alpha", [secret])
        beta = await make(db, "beta", ["Beta's own unrelated content about catering."])

        # Beta searches for Alpha's secret, using Alpha's exact wording -- so
        # the embedding is an exact match and only the tenant filter can
        # prevent the hit.
        results = await RetrievalService.search(
            db=db, query=secret, org_id=beta.org_id, min_score=0.0
        )

        contents = [r["content"] for r in results]
        assert secret not in contents
        assert all(r["document_id"] != alpha.document_id for r in results)

    async def test_the_owning_organisation_does_find_it(self, db):
        """
        The necessary other half. A filter that returns nothing to everyone
        would pass the test above while breaking the product.
        """
        secret = "The acquisition price agreed with Northwind is 4.2 million."
        alpha = await make(db, "alpha", [secret])

        results = await RetrievalService.search(
            db=db, query=secret, org_id=alpha.org_id, min_score=0.0
        )

        assert secret in [r["content"] for r in results]

    async def test_an_organisation_with_no_documents_gets_nothing(self, db):
        await make(db, "alpha", ["Some content that does exist somewhere."])

        results = await RetrievalService.search(
            db=db, query="Some content that does exist somewhere.",
            org_id=uuid.uuid4(), min_score=0.0,
        )

        assert results == []

    async def test_identical_text_in_two_tenants_stays_separated(self, db):
        """
        The hardest case for a post-filter to get right, and the one a join
        handles for free: both tenants hold the SAME text, so the vectors are
        identical and only ownership distinguishes them.
        """
        shared = "Standard terms and conditions apply to all engagements."

        alpha = await make(db, "alpha", [shared])
        beta = await make(db, "beta", [shared])

        alpha_results = await RetrievalService.search(
            db=db, query=shared, org_id=alpha.org_id, min_score=0.0
        )
        beta_results = await RetrievalService.search(
            db=db, query=shared, org_id=beta.org_id, min_score=0.0
        )

        assert all(r["document_id"] == alpha.document_id for r in alpha_results)
        assert all(r["document_id"] == beta.document_id for r in beta_results)
        assert alpha_results and beta_results


# ======================================================================
# Search behaviour
# ======================================================================


class TestSearchResults:
    async def test_a_chunks_own_text_retrieves_that_chunk(self, db):
        """
        The most basic guarantee there is. If this fails, retrieval is broken
        at the plumbing level and nothing downstream can work.
        """
        target = "Employees accrue 1.75 days of paid leave per month."
        corpus = await make(db, "alpha", [target, "Unrelated text about parking permits."])

        results = await RetrievalService.search(
            db=db, query=target, org_id=corpus.org_id, min_score=0.0
        )

        assert results
        assert results[0]["content"] == target

    async def test_results_are_ordered_by_similarity(self, db):
        corpus = await make(db, "alpha", [f"Distinct content number {i}." for i in range(6)])

        results = await RetrievalService.search(
            db=db, query="Distinct content number 3.", org_id=corpus.org_id,
            top_k=6, min_score=0.0,
        )

        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    async def test_a_result_carries_everything_a_citation_needs(self, db):
        """
        Filename and page must survive the query, because they are what a
        citation card displays. Losing them does not break search -- it breaks
        the ability to check an answer.
        """
        corpus = await make(db, "alpha", ["Some findable content here."])

        result = (
            await RetrievalService.search(
                db=db, query="Some findable content here.",
                org_id=corpus.org_id, min_score=0.0,
            )
        )[0]

        assert result["filename"] == "alpha.pdf"
        assert result["page_number"] == 1
        assert result["chunk_index"] == 0
        assert result["document_id"] == corpus.document_id
        assert isinstance(result["score"], float)

    async def test_top_k_limits_the_number_of_results(self, db):
        corpus = await make(db, "alpha", [f"Content item number {i}." for i in range(10)])

        for k in (1, 3, 5):
            results = await RetrievalService.search(
                db=db, query="Content item", org_id=corpus.org_id, top_k=k, min_score=0.0
            )
            assert len(results) <= k

    async def test_top_k_of_zero_returns_nothing(self, db):
        """
        Guards the `x if x is not None else default` idiom in the source.
        Written as `top_k or default`, a deliberate 0 would be swallowed and
        silently replaced by 5.
        """
        corpus = await make(db, "alpha", ["Some content."])

        results = await RetrievalService.search(
            db=db, query="Some content.", org_id=corpus.org_id, top_k=0, min_score=0.0
        )

        assert results == []

    async def test_scores_are_similarities_not_distances(self, db):
        """
        Reported as `1 - cosine_distance`, so higher means closer. Inverted,
        every ranking in the product would be exactly backwards.
        """
        corpus = await make(db, "alpha", ["An exactly matching sentence."])

        results = await RetrievalService.search(
            db=db, query="An exactly matching sentence.", org_id=corpus.org_id, min_score=0.0
        )

        assert results[0]["score"] > 0.99

    async def test_min_score_discards_weak_matches(self, db):
        """
        The setting that lets the system answer "I don't know". Vector search
        always returns SOMETHING; without a floor, the nearest chunk to an
        unanswerable question is still returned and still irrelevant.
        """
        corpus = await make(db, "alpha", ["Content about annual leave policy."])

        everything = await RetrievalService.search(
            db=db, query="a completely unrelated question about spacecraft",
            org_id=corpus.org_id, min_score=0.0,
        )
        filtered = await RetrievalService.search(
            db=db, query="a completely unrelated question about spacecraft",
            org_id=corpus.org_id, min_score=0.999,
        )

        assert len(filtered) <= len(everything)
        assert filtered == []


class TestFilters:
    async def test_project_id_narrows_the_search(self, db):
        """
        Scoping a chat to one project is why a conversation in the HR project
        does not answer from the finance documents.
        """
        alpha = await make(db, "alpha", ["Content that lives in the first project."])

        # A second project in the SAME organisation, so only project_id
        # separates them.
        second_project = Project(name="Second", org_id=alpha.org_id)
        db.add(second_project)
        await db.flush()
        await db.commit()

        results = await RetrievalService.search(
            db=db, query="Content that lives in the first project.",
            org_id=alpha.org_id, project_id=second_project.id, min_score=0.0,
        )

        assert results == []

    async def test_document_id_narrows_further(self, db):
        alpha = await make(db, "alpha", ["Findable content in document one."])

        results = await RetrievalService.search(
            db=db, query="Findable content in document one.",
            org_id=alpha.org_id, document_id=uuid.uuid4(), min_score=0.0,
        )

        assert results == []

    async def test_the_owning_document_id_still_matches(self, db):
        alpha = await make(db, "alpha", ["Findable content in document one."])

        results = await RetrievalService.search(
            db=db, query="Findable content in document one.",
            org_id=alpha.org_id, document_id=alpha.document_id, min_score=0.0,
        )

        assert len(results) == 1


class TestOnlyCompletedDocuments:
    @pytest.mark.parametrize("status", ["pending", "processing", "failed"])
    async def test_an_unfinished_document_is_not_searched(self, db, status):
        """
        A document mid-chunking has partial embeddings. Including it would
        answer from half a file without saying so -- an answer that is wrong
        in a way the user cannot detect, because the citation looks fine.
        """
        corpus = await make(db, status, ["Content that is still being processed."], status=status)

        results = await RetrievalService.search(
            db=db, query="Content that is still being processed.",
            org_id=corpus.org_id, min_score=0.0,
        )

        assert results == []

    async def test_a_completed_document_is_searched(self, db):
        corpus = await make(db, "done", ["Content that finished processing."], status="completed")

        results = await RetrievalService.search(
            db=db, query="Content that finished processing.",
            org_id=corpus.org_id, min_score=0.0,
        )

        assert len(results) == 1


# ======================================================================
# The cascade the schema promises
# ======================================================================


class TestOwnershipCascade:
    async def test_deleting_a_project_removes_its_chunks(self, db):
        """
        `ondelete="CASCADE"` is declared in models/, but a declaration is only
        a promise until the migration has actually created the constraint.
        Orphaned chunks would stay searchable forever.
        """
        corpus = await make(db, "alpha", ["Content that should disappear with its project."])
        chunk_id = corpus.chunks[0].id

        await db.execute(delete(Project).where(Project.id == corpus.project_id))
        await db.commit()

        remaining = await db.execute(select(Chunk).where(Chunk.id == chunk_id))
        assert remaining.scalar_one_or_none() is None

    async def test_deleting_a_document_removes_its_embeddings(self, db):
        corpus = await make(db, "alpha", ["Content with an embedding attached."])
        chunk_id = corpus.chunks[0].id

        await db.execute(delete(Document).where(Document.id == corpus.document_id))
        await db.commit()

        remaining = await db.execute(
            select(ChunkEmbedding).where(ChunkEmbedding.chunk_id == chunk_id)
        )
        assert remaining.scalar_one_or_none() is None


# ======================================================================
# Hybrid search: the Postgres full-text half
# ======================================================================


class TestHybridSearch:
    async def test_an_exact_term_is_found_by_keyword_even_below_min_score(self, db):
        """
        The case hybrid search exists for: a rare code that an embedding
        barely registers. With a min_score no semantic match can reach, only
        the keyword search can find it -- and it must.
        """
        corpus = await make(db, "alpha", [
            "General guidance about travel and accommodation.",
            "Policy FIN-204 caps hotel costs at 180 euros per night.",
        ])

        results = await RetrievalService.search(
            db=db, query="FIN-204", org_id=corpus.org_id, min_score=0.999, hybrid=True
        )

        assert [r["content"] for r in results] == [corpus.chunks[1].content]
        assert results[0]["match_type"] in ("keyword", "both")

    async def test_vectors_only_does_not(self, db):
        corpus = await make(db, "alpha", ["Policy FIN-204 caps hotel costs."])
        results = await RetrievalService.search(
            db=db, query="FIN-204", org_id=corpus.org_id, min_score=0.999, hybrid=False
        )
        assert results == []

    async def test_keyword_hits_respect_the_tenant_filter(self, db):
        owner = await make(db, "alpha", ["Policy FIN-204 caps hotel costs."])
        stranger = await make(db, "beta", ["Nothing relevant here."])

        results = await RetrievalService.search(
            db=db, query="FIN-204", org_id=stranger.org_id, min_score=0.0, hybrid=True
        )

        assert all(r["document_id"] != owner.document_id for r in results)

    async def test_a_chunk_found_by_both_is_reported_once(self, db):
        corpus = await make(db, "alpha", ["Annual leave is twenty five days."])
        results = await RetrievalService.search(
            db=db, query="Annual leave is twenty five days.", org_id=corpus.org_id,
            min_score=0.0, hybrid=True,
        )
        assert len(results) == 1 and results[0]["match_type"] == "both"

    async def test_the_document_filter_narrows_both_halves(self, db):
        corpus = await make(db, "alpha", ["Policy FIN-204 caps hotel costs."])
        unrelated = await make(db, "beta", ["Policy FIN-204 also appears here."])

        kept = await RetrievalService.search(
            db=db, query="FIN-204", org_id=corpus.org_id, min_score=0.0, hybrid=True,
            document_ids=[corpus.document_id],
        )
        # A filter naming only a document the search cannot see narrows to
        # nothing -- it never widens past the tenant filter.
        narrowed = await RetrievalService.search(
            db=db, query="FIN-204", org_id=corpus.org_id, min_score=0.0, hybrid=True,
            document_ids=[unrelated.document_id],
        )

        assert {r["document_id"] for r in kept} == {corpus.document_id}
        assert narrowed == []
