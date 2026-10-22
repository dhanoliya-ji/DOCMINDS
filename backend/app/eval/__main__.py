"""
python -m app.eval -- run a golden set against a real project.

    cd backend
    python -m app.eval --golden app/eval/golden_example.json --project-id <uuid>

    options:
      --top-k N           results per question (default: the golden set's
                          "top_k", else RETRIEVAL_TOP_K)
      --answers           also generate answers and score them (keywords,
                          and faithfulness by an LLM judge when a key is set)
      --no-hybrid         vectors only, to compare against hybrid search
      --output FILE       also write the full report as JSON
      --min-hit-rate X    exit with status 1 below this (for CI gates)

It talks to the database in DATABASE_URL / POSTGRES_* like the API does, and
only ever READS: nothing is saved, nothing is metered.
"""

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any, Dict, List

from app.eval.harness import (
    GoldenSetError,
    format_report,
    llm_judge,
    load_golden_set,
    run_evaluation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.eval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden", required=True, help="Path to the golden-set JSON file.")
    parser.add_argument("--project-id", required=True, type=uuid.UUID,
                        help="The project whose documents the questions are about.")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--answers", action="store_true",
                        help="Generate answers and score them too (uses model tokens).")
    parser.add_argument("--no-hybrid", action="store_true", help="Vector search only.")
    parser.add_argument("--output", help="Write the full JSON report here.")
    parser.add_argument("--min-hit-rate", type=float, default=None)
    return parser


async def main(argv: List[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        golden = load_golden_set(args.golden)
    except (GoldenSetError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Imported after argument parsing so `--help` works without a database.
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.models.project import Project
    from app.services.llm import LLMService
    from app.services.retrieval import RetrievalService

    top_k = args.top_k or golden.top_k or settings.RETRIEVAL_TOP_K
    hybrid = False if args.no_hybrid else settings.RETRIEVAL_HYBRID

    async with SessionLocal() as db:
        project = await db.get(Project, args.project_id)
        if project is None:
            print(f"error: no project {args.project_id}", file=sys.stderr)
            return 2

        async def search(question: str, k: int) -> List[Dict[str, Any]]:
            return await RetrievalService.search(
                db=db, query=question, org_id=project.org_id,
                project_id=project.id, top_k=k, hybrid=hybrid,
            )

        async def answer(question: str, chunks: List[Dict[str, Any]]) -> str:
            result = await LLMService.generate_answer(question=question, chunks=chunks, history=[])
            return result["answer"]

        report = await run_evaluation(
            golden,
            search,
            top_k=top_k,
            answer_fn=answer if args.answers else None,
            judge_fn=llm_judge if args.answers else None,
            settings_snapshot={
                "hybrid": hybrid,
                "reranker": settings.RERANKER,
                "min_score": settings.RETRIEVAL_MIN_SCORE,
                "embedding_provider": settings.EMBEDDING_PROVIDER,
                "llm_model": settings.GROQ_MODEL if settings.GROQ_API_KEY else "mock",
            },
        )

    print(format_report(report))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=2, default=str)
        print(f"\nFull report written to {args.output}")

    if args.min_hit_rate is not None and report.hit_rate < args.min_hit_rate:
        print(f"\nHit rate {report.hit_rate:.3f} is below the required {args.min_hit_rate:.3f}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(main(sys.argv[1:])))
