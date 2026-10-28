import json

import pytest

from app.api.v1.chat import make_title, sse
from app.core.config import settings
from app.eval import harness
from app.eval.__main__ import main as eval_main
from app.services import export, query_rewrite, reranker
from app.services.retrieval import match_type, reciprocal_rank_fusion


class TestReciprocalRankFusion:
    def test_agreement_beats_a_single_first_place(self):
        scores = reciprocal_rank_fusion([["a", "b"], ["c", "b"]], k=60)
        assert max(scores, key=scores.get) == "b"

    def test_the_formula(self):
        scores = reciprocal_rank_fusion([["a"], ["a"]], k=60)
        assert scores["a"] == pytest.approx(2 / 61)

    def test_an_item_in_one_list_still_counts(self):
        scores = reciprocal_rank_fusion([["a"], []], k=60)
        assert scores == {"a": pytest.approx(1 / 61)}

    def test_empty_input(self):
        assert reciprocal_rank_fusion([[], []]) == {}

    def test_a_smaller_k_favours_the_top_more(self):
        top_heavy = reciprocal_rank_fusion([["a", "b"]], k=1)
        flat = reciprocal_rank_fusion([["a", "b"]], k=1000)
        assert top_heavy["a"] / top_heavy["b"] > flat["a"] / flat["b"]

    @pytest.mark.parametrize(
        "vector,keyword,expected",
        [(True, True, "both"), (True, False, "vector"), (False, True, "keyword")],
    )
    def test_match_type(self, vector, keyword, expected):
        assert match_type(vector, keyword) == expected


def _matches(*texts):
    return [{"chunk_id": i, "content": text, "score": 0.5} for i, text in enumerate(texts)]


class TestReranker:
    def test_it_sorts_by_the_scorer(self):
        def scorer(pairs):
            return [len(passage) for _question, passage in pairs]

        ranked = reranker.rerank("q", _matches("aa", "aaaa", "a"), scorer=scorer)
        assert [m["content"] for m in ranked] == ["aaaa", "aa", "a"]
        assert ranked[0]["rerank_score"] == 4.0

    def test_the_scorer_sees_the_question_with_each_passage(self):
        seen = []

        def scorer(pairs):
            seen.extend(pairs)
            return [0.0] * len(pairs)

        reranker.rerank("annual leave", _matches("x", "y"), scorer=scorer)
        assert seen == [("annual leave", "x"), ("annual leave", "y")]

    def test_a_crashing_scorer_keeps_the_order(self):
        def scorer(pairs):
            raise RuntimeError("model exploded")

        original = _matches("first", "second")
        assert reranker.rerank("q", original, scorer=scorer) == original

    def test_a_wrong_number_of_scores_keeps_the_order(self):
        original = _matches("first", "second")
        assert reranker.rerank("q", original, scorer=lambda pairs: [1.0]) == original

    def test_no_model_available_keeps_the_order(self, monkeypatch):
        monkeypatch.setattr(reranker, "_load_cross_encoder", lambda name: None)
        original = _matches("first", "second")
        assert reranker.rerank("q", original) == original

    def test_off_by_default(self):
        assert settings.RERANKER == "off"


HISTORY = [
    {"role": "user", "content": "How many days of annual leave do full-time staff get?"},
    {"role": "assistant", "content": "Full-time staff get 25 days [1]."},
]


@pytest.fixture
def keyless(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(settings, "QUERY_REWRITE_ENABLED", True)


class TestQueryRewrite:
    @pytest.mark.parametrize(
        "question",
        ["and part-timers?", "What about contractors?", "Does it apply to them as well?", "why?"],
    )
    def test_follow_ups_need_context(self, question):
        assert query_rewrite.needs_context(question)

    def test_a_standalone_question_does_not(self):
        assert not query_rewrite.needs_context(
            "What is the expense limit for international travel bookings?"
        )

    async def test_a_follow_up_gets_the_previous_question(self, keyless):
        result = await query_rewrite.rewrite_query("and part-timers?", HISTORY)
        assert result.rewritten and result.method == "heuristic"
        assert "annual leave" in result.query
        assert result.query.endswith("and part-timers?")

    async def test_the_first_question_is_never_rewritten(self, keyless):
        result = await query_rewrite.rewrite_query("and part-timers?", [])
        assert result.query == "and part-timers?" and not result.rewritten

    async def test_a_standalone_follow_up_is_left_alone(self, keyless):
        question = "What is the expense limit for international travel bookings?"
        result = await query_rewrite.rewrite_query(question, HISTORY)
        assert result.query == question and not result.rewritten

    async def test_it_can_be_switched_off(self, keyless, monkeypatch):
        monkeypatch.setattr(settings, "QUERY_REWRITE_ENABLED", False)
        result = await query_rewrite.rewrite_query("and part-timers?", HISTORY)
        assert result.query == "and part-timers?"

    async def test_a_copy_of_the_current_question_in_history_is_skipped(self, keyless):
        history = HISTORY + [{"role": "user", "content": "and part-timers?"}]
        result = await query_rewrite.rewrite_query("and part-timers?", history)
        assert result.query.startswith("How many days")

    async def test_the_model_rewrite_is_used_when_a_key_is_set(self, monkeypatch):
        from types import SimpleNamespace

        from app.services.llm import LLMService

        monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
        reply = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='"annual leave for part-time staff"'))],
            usage=SimpleNamespace(prompt_tokens=40, completion_tokens=6),
        )

        async def fake_complete(client, messages, **extra):
            assert "part-timers" in messages[-1]["content"]
            return reply, "test-model"

        monkeypatch.setattr(LLMService, "_client", classmethod(lambda cls: object()))
        monkeypatch.setattr(LLMService, "_complete", staticmethod(fake_complete))

        result = await query_rewrite.rewrite_query("and part-timers?", HISTORY)
        assert result.query == "annual leave for part-time staff"
        assert result.method == "llm" and result.prompt_tokens == 40 and result.completion_tokens == 6

    async def test_a_failed_model_call_falls_back_to_the_heuristic(self, monkeypatch):
        from app.services.llm import LLMService

        monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
        monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")

        async def failing(client, messages, **extra):
            raise ConnectionError("down")

        monkeypatch.setattr(LLMService, "_client", classmethod(lambda cls: object()))
        monkeypatch.setattr(LLMService, "_complete", staticmethod(failing))

        result = await query_rewrite.rewrite_query("and part-timers?", HISTORY)
        assert result.method == "heuristic" and "annual leave" in result.query


class TestTitles:
    def test_whitespace_is_collapsed(self):
        assert make_title("  How many\n days   of leave? ") == "How many days of leave?"

    def test_a_long_question_is_cut_at_a_word(self):
        title = make_title("word " * 40)
        assert title.endswith("...")
        assert len(title) <= 63
        assert "wor..." not in title

    def test_an_empty_question_keeps_the_placeholder(self):
        assert make_title("   ") == "New Chat"


class TestSse:
    def test_the_frame(self):
        assert sse("token", {"text": "Hi"}) == 'event: token\ndata: {"text": "Hi"}\n\n'

    def test_newlines_stay_inside_one_data_line(self):
        frame = sse("token", {"text": "line one\nline two"})
        assert frame.count("\n") == 3  # event line, data line, blank line


class TestExport:
    def test_a_conversation(self):
        import datetime

        markdown = export.conversation_to_markdown(
            "Leave questions",
            [
                {"role": "user", "content": "How much leave?"},
                {
                    "role": "assistant",
                    "content": "25 days [1].",
                    "mode": "rag",
                    "model": "gpt-oss",
                    "citations": [
                        {"number": 1, "filename": "handbook.pdf", "page_number": 12,
                         "snippet": 'Staff get "25 days".'}
                    ],
                },
            ],
            exported_at=datetime.datetime(2026, 10, 23, 14, 2),
        )
        assert markdown.startswith("# Leave questions\n")
        assert "_Exported from DocMinds on 2026-10-23 14:02 UTC_" in markdown
        assert "## Question\n\nHow much leave?" in markdown
        assert "## Answer  (Quick answer · gpt-oss)" in markdown
        assert "1. handbook.pdf, page 12 -- \"Staff get '25 days'.\"" in markdown

    def test_safe_filename(self):
        assert export.safe_filename("Q3: Leave / Policy?") == "q3-leave-policy.md"
        assert export.safe_filename("") == "conversation.md"


GOLDEN = {
    "name": "test",
    "cases": [
        {"id": "a", "question": "leave?", "expected_documents": ["handbook.pdf"],
         "expected_snippets": ["25 days"], "expected_keywords": ["25"]},
        {"id": "b", "question": "expenses?", "expected_documents": ["policy.docx"]},
        {"id": "none", "question": "ticker?", "expected_documents": []},
    ],
}


def _result(filename, content="text"):
    return {"filename": filename, "content": content, "page_number": 1}


class TestGoldenSet:
    def test_it_loads(self):
        golden = harness.parse_golden_set(GOLDEN)
        assert [c.id for c in golden.cases] == ["a", "b", "none"]

    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"cases": []},
            {"cases": [{"id": "x"}]},
            {"cases": [{"question": "q", "expected_documents": "not a list"}]},
            {"cases": [{"id": "x", "question": "q"}, {"id": "x", "question": "r"}]},
        ],
    )
    def test_bad_files_are_refused(self, bad):
        with pytest.raises(harness.GoldenSetError):
            harness.parse_golden_set(bad)

    def test_the_example_file_is_valid(self):
        from pathlib import Path

        import app.eval

        path = Path(app.eval.__file__).parent / "golden_example.json"
        assert len(harness.load_golden_set(path).cases) >= 3


class TestMetrics:
    def test_relevance_needs_the_document_and_a_snippet(self):
        case = harness.parse_golden_set(GOLDEN).cases[0]
        assert harness.is_relevant(case, _result("handbook.pdf", "You get 25  DAYS of leave"))
        assert not harness.is_relevant(case, _result("handbook.pdf", "unrelated"))
        assert not harness.is_relevant(case, _result("other.pdf", "25 days"))

    def test_rank_and_hit(self):
        case = harness.parse_golden_set(GOLDEN).cases[1]
        results = [_result("x.pdf"), _result("policy.docx")]
        assert harness.first_relevant_rank(case, results) == 2
        assert harness.retrieval_hit(case, results)

    def test_a_should_find_nothing_case(self):
        case = harness.parse_golden_set(GOLDEN).cases[2]
        assert harness.retrieval_hit(case, [])
        assert not harness.retrieval_hit(case, [_result("handbook.pdf")])

    def test_keywords(self):
        case = harness.parse_golden_set(GOLDEN).cases[0]
        assert harness.keywords_present(case, "It is 25 days.") is True
        assert harness.keywords_present(case, "Not sure.") is False
        assert harness.keywords_present(harness.parse_golden_set(GOLDEN).cases[1], "x") is None

    @pytest.mark.parametrize(
        "reply,expected",
        [('{"score": 0.8, "reason": "ok"}', 0.8), ("Sure! {\"score\": 3}", 1.0),
         ("no json here", None), ('{"score": "high"}', None)],
    )
    def test_judge_reply_parsing(self, reply, expected):
        assert harness.parse_judge_reply(reply) == expected


class TestRun:
    async def test_a_full_run(self):
        golden = harness.parse_golden_set(GOLDEN)
        canned = {
            "leave?": [_result("other.pdf"), _result("handbook.pdf", "25 days a year")],
            "expenses?": [_result("x.pdf")],
            "ticker?": [],
        }

        async def search(question, top_k):
            return canned[question]

        async def answer(question, chunks):
            return "You get 25 days." if question == "leave?" else "Not found."

        async def judge(question, chunks, text):
            return 1.0

        report = await harness.run_evaluation(
            golden, search, top_k=3, answer_fn=answer, judge_fn=judge
        )
        assert report.hit_rate == pytest.approx(2 / 3, abs=1e-3)
        assert report.mrr == pytest.approx(0.25)
        assert report.keyword_rate == 1.0
        assert report.faithfulness == 1.0
        assert "retrieval hit rate" in harness.format_report(report)

    async def test_without_answers_generation_is_not_scored(self):
        golden = harness.parse_golden_set(GOLDEN)

        async def search(question, top_k):
            return []

        report = await harness.run_evaluation(golden, search)
        assert report.keyword_rate is None and report.faithfulness is None

    async def test_the_judge_needs_a_key(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
        assert await harness.llm_judge("q", [_result("a.pdf")], "answer") is None


class TestCli:
    async def test_a_missing_file_is_an_error(self, tmp_path, capsys):
        code = await eval_main(["--golden", str(tmp_path / "nope.json"),
                                "--project-id", "00000000-0000-0000-0000-000000000001"])
        assert code == 2
        assert "error" in capsys.readouterr().err

    async def test_an_invalid_file_is_an_error(self, tmp_path, capsys):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"cases": []}), encoding="utf-8")
        code = await eval_main(["--golden", str(path),
                                "--project-id", "00000000-0000-0000-0000-000000000001"])
        assert code == 2
