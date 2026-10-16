"""
llm.py
======
WHAT THIS FILE DOES
-------------------
This is the "G" in RAG -- GENERATION.

By the time we get here, `retrieval.py` has already found the handful of
document paragraphs most likely to contain the answer. This file's job is to
hand those paragraphs plus the user's question to a Large Language Model and
get back a written, human-readable answer.

THE ENTIRE IDEA OF RAG IN FOUR LINES
------------------------------------
    1. RETRIEVE : find the paragraphs relevant to the question   (retrieval.py)
    2. AUGMENT  : paste those paragraphs into the prompt         (this file)
    3. GENERATE : the LLM writes an answer using only that text  (this file)
    4. CITE     : we report which paragraphs were used           (chat.py)

Why bother? An LLM on its own has never seen your company's documents, so if
you ask about your internal leave policy it will confidently invent one -- this
is called hallucination. By pasting the real text into the prompt and
instructing the model to use nothing else, the answer becomes grounded in your
actual documents, and every claim can be traced back to a page number.

WHICH MODEL AND WHY
-------------------
We use Groq, which serves open models such as GPT-OSS 120B.
  * Free tier, no credit card required -> perfect for a portfolio project.
  * Extremely fast (hundreds of tokens per second), so the chat UI feels alive.
  * Its SDK mirrors OpenAI's, so switching providers later is a small change.

GET YOUR FREE KEY:  https://console.groq.com/keys
Then put it in `backend/.env` as:   GROQ_API_KEY=gsk_your_key_here
"""

import time
from typing import Any, Dict, Generator, List, Optional

from app.core.config import settings

# ----------------------------------------------------------------------
# THE SYSTEM PROMPT
# ----------------------------------------------------------------------
# A "system prompt" sets the model's standing instructions before it ever sees
# the user's question. It is the single most important piece of text in a RAG
# application, because it is what forces the model to stay honest.
#
# Every rule below exists to prevent one specific failure mode:
#   Rule 1-2 : stop the model answering from its own training data
#   Rule 3   : make it admit ignorance rather than invent an answer
#   Rule 4   : make each claim traceable to a source
#   Rule 5   : keep answers readable
# ----------------------------------------------------------------------
SYSTEM_PROMPT = """You are a precise enterprise document analyst.

You will be given NUMBERED EXCERPTS from a company's internal documents,
followed by a QUESTION from an employee.

Follow these rules strictly:

1. Answer using ONLY information found in the excerpts provided. Do not use
   outside knowledge, and do not guess.
2. If the excerpts do not contain enough information to answer, reply exactly:
   "I could not find that information in the uploaded documents."
   Do not attempt a partial or speculative answer.
3. Never invent facts, figures, dates, names, or policies.
4. Cite the excerpt you used for each claim with its bracketed number, like
   [1] or [2]. Place the citation immediately after the sentence it supports.
5. Be concise and direct. Use short paragraphs or bullet points. Do not repeat
   the question back to the user, and do not add a preamble.
"""


class LLMService:
    """
    Wraps the chat model behind two simple methods.

    Everything else in the codebase calls `generate_answer(...)` and never
    needs to know that Groq exists. If we later move to a different provider,
    only this file changes.
    """

    # ==================================================================
    # STEP 1: BUILD THE PROMPT  (the "Augment" in Retrieval-Augmented)
    # ==================================================================

    @staticmethod
    def build_context_prompt(question: str, chunks: List[Dict[str, Any]]) -> str:
        """
        Assemble the retrieved chunks and the question into one block of text.

        `chunks` is what retrieval.py returned -- a list of dictionaries, each
        holding the chunk's text plus where it came from.

        The output looks like this:

            EXCERPT [1] (source: handbook.pdf, page 12):
            Employees accrue 1.75 days of paid leave per month...

            EXCERPT [2] (source: policy.docx, page 3):
            Unused leave may be carried over...

            QUESTION: How much annual leave do I get?

        Numbering the excerpts is what makes citations possible: the model
        writes "[1]" in its answer, and the frontend can map that number back
        to the exact file and page for the user to verify.
        """
        # If retrieval found nothing, say so explicitly. An empty context block
        # would leave the model free to fall back on its own training data,
        # which is exactly the hallucination we are trying to prevent.
        if not chunks:
            return (
                "EXCERPTS: (none found)\n\n"
                f"QUESTION: {question}"
            )

        parts = []
        # `enumerate(chunks, 1)` walks the list while counting from 1 rather
        # than 0, because humans expect citations to start at [1].
        for index, chunk in enumerate(chunks, 1):
            # `.get(key, default)` reads a dictionary key safely -- if the key
            # is missing we get the default instead of a KeyError crash.
            filename = chunk.get("filename", "unknown file")
            page = chunk.get("page_number")

            # Page numbers are genuinely absent for formats that have no pages,
            # such as a CSV or an email, so only mention the page when we have one.
            location = f"{filename}, page {page}" if page else filename

            parts.append(
                f"EXCERPT [{index}] (source: {location}):\n{chunk.get('content', '')}"
            )

        # A blank line between excerpts helps the model see them as separate
        # documents rather than one run-on passage.
        excerpt_block = "\n\n".join(parts)

        return f"{excerpt_block}\n\nQUESTION: {question}"

    # ==================================================================
    # STEP 2: CALL THE MODEL AND GET AN ANSWER
    # ==================================================================

    @classmethod
    def generate_answer(
        cls,
        question: str,
        chunks: List[Dict[str, Any]],
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Produce the final written answer.

        Arguments:
            question -- what the user typed.
            chunks   -- the relevant excerpts found by retrieval.py.
            history  -- earlier turns in this conversation, so the user can ask
                        a follow-up like "and what about part-time staff?"
                        without repeating the whole question. Format:
                        [{"role": "user", "content": "..."},
                         {"role": "assistant", "content": "..."}]

        Returns a dictionary containing the answer text plus metrics we store
        on the ChatMessage row for the analytics view:
            {"answer": str, "tokens_used": int, "latency": float, "model": str}
        """
        # `time.perf_counter()` is a high-precision stopwatch. We record the
        # start now and subtract at the end to measure how long the model took.
        # This becomes the `latency` column on ChatMessage.
        started_at = time.perf_counter()

        # If no key is configured, or the provider is deliberately set to mock,
        # return a canned response. This lets the whole application be demoed
        # end to end -- upload, chunk, embed, retrieve -- without any API key.
        if settings.LLM_PROVIDER == "mock" or not settings.GROQ_API_KEY:
            return cls._mock_answer(question, chunks, started_at)

        try:
            # Imported inside the function so the package is only needed by
            # deployments that actually use Groq.
            from groq import Groq

            client = Groq(api_key=settings.GROQ_API_KEY)

            # Chat models take a LIST of messages, each tagged with a role:
            #   "system"    -> standing instructions (highest authority)
            #   "user"      -> what the human said
            #   "assistant" -> what the model previously replied
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": SYSTEM_PROMPT}
            ]

            # Add prior conversation turns so follow-up questions make sense.
            if history:
                # Only the last 6 messages (3 exchanges). Older context is
                # dropped to keep the prompt short: every token costs time and
                # counts against the model's context window.
                messages.extend(history[-6:])

            # Finally the current question, with the retrieved excerpts pasted
            # in above it. This is the "augmented" prompt.
            messages.append(
                {"role": "user", "content": cls.build_context_prompt(question, chunks)}
            )

            completion = client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=messages,
                # Low temperature = factual and repeatable, not creative.
                temperature=settings.LLM_TEMPERATURE,
                max_tokens=settings.LLM_MAX_TOKENS,
            )

            answer = completion.choices[0].message.content or ""

            # The API reports exactly how many tokens the request consumed.
            # We store it so the UI can show real usage per message.
            tokens_used = 0
            if completion.usage:
                tokens_used = completion.usage.total_tokens

            return {
                "answer": answer.strip(),
                "tokens_used": tokens_used,
                "latency": round(time.perf_counter() - started_at, 3),
                "model": settings.GROQ_MODEL,
            }

        except ImportError:
            print("[LLM] The 'groq' package is not installed. Run: pip install groq")
            return cls._mock_answer(question, chunks, started_at)
        except Exception as exc:
            # A network blip, an expired key or a rate limit should surface as
            # a readable message in the chat, not a 500 error that loses the
            # user's question entirely.
            print(f"[LLM] Groq call failed: {exc}")
            return {
                "answer": (
                    "The AI service is temporarily unavailable, so I could not "
                    "generate an answer. The relevant document excerpts are "
                    "still listed below as sources."
                ),
                "tokens_used": 0,
                "latency": round(time.perf_counter() - started_at, 3),
                "model": "error",
            }

    # ==================================================================
    # STEP 2b: THE SAME THING, BUT STREAMED WORD BY WORD
    # ==================================================================

    @classmethod
    def stream_answer(
        cls,
        question: str,
        chunks: List[Dict[str, Any]],
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Generator[str, None, None]:
        """
        Yield the answer in small pieces as the model writes it.

        This is what produces the "typing" effect in the chat UI. Instead of
        the user staring at a spinner for three seconds and then seeing a wall
        of text, words appear as they are generated.

        `yield` makes this function a GENERATOR: it hands back one piece and
        pauses, resuming only when the caller asks for the next piece. FastAPI
        forwards each piece to the browser immediately.
        """
        if settings.LLM_PROVIDER == "mock" or not settings.GROQ_API_KEY:
            # Mimic streaming by emitting the canned answer word by word, so
            # the frontend behaves identically with or without an API key.
            mock = cls._mock_answer(question, chunks, time.perf_counter())
            for word in mock["answer"].split(" "):
                yield word + " "
            return

        try:
            from groq import Groq

            client = Groq(api_key=settings.GROQ_API_KEY)

            messages: List[Dict[str, str]] = [
                {"role": "system", "content": SYSTEM_PROMPT}
            ]
            if history:
                messages.extend(history[-6:])
            messages.append(
                {"role": "user", "content": cls.build_context_prompt(question, chunks)}
            )

            # `stream=True` is the only difference from generate_answer above.
            stream = client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=messages,
                temperature=settings.LLM_TEMPERATURE,
                max_tokens=settings.LLM_MAX_TOKENS,
                stream=True,
            )

            for piece in stream:
                # Each piece carries a `delta` -- only the NEW text since the
                # last piece, not the whole answer so far. The final piece has
                # `content` set to None, which is why we check before yielding.
                token = piece.choices[0].delta.content
                if token:
                    yield token

        except Exception as exc:
            print(f"[LLM] Streaming failed: {exc}")
            yield "The AI service is temporarily unavailable."

    # ==================================================================
    # FALLBACK used when no API key is configured
    # ==================================================================

    @staticmethod
    def _mock_answer(
        question: str, chunks: List[Dict[str, Any]], started_at: float
    ) -> Dict[str, Any]:
        """
        Build a useful answer WITHOUT calling any AI service.

        This is deliberately more than a placeholder: it echoes back the real
        text that retrieval actually found. That means a reviewer who clones
        this repo with no API key can still see the retrieval half of the
        system genuinely working, which is the harder half to build.
        """
        if not chunks:
            answer = "I could not find that information in the uploaded documents."
        else:
            best = chunks[0]
            # Show only the opening 400 characters so the reply stays readable.
            preview = best.get("content", "")[:400]
            answer = (
                "[Demo mode -- no GROQ_API_KEY configured, so this text is "
                "returned directly from the top search result rather than "
                "written by an AI.]\n\n"
                f"The most relevant passage found was in "
                f"'{best.get('filename', 'a document')}':\n\n{preview}..."
            )

        return {
            "answer": answer,
            "tokens_used": 0,
            "latency": round(time.perf_counter() - started_at, 3),
            "model": "mock",
        }
