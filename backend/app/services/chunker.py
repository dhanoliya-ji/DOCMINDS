"""
chunker.py
==========
WHAT THIS FILE DOES
-------------------
It cuts a long document into small, self-contained pieces called CHUNKS.

    a 40-page handbook  ->  ~120 chunks of roughly 400 tokens each

WHY NOT JUST EMBED THE WHOLE DOCUMENT?
--------------------------------------
Two reasons, and the second is the more important one.

  1. SIZE. A language model can only read so much text at once. A 300-page
     manual will not fit into a prompt, full stop.

  2. PRECISION. This is the real reason. An embedding is a single point in
     "meaning space". If you embed an entire book, you get one point meaning
     "a book about many things" -- which sits close to nothing in particular
     and matches every query equally weakly.

     Embed one paragraph and you get a point representing ONE clear idea. It
     matches sharply when it is relevant and not at all when it is not.

     Chunking is also what lets an answer say "page 12" instead of "somewhere
     in this 300-page file".

WHAT MAKES THIS "TOKEN-AWARE"
-----------------------------
Models do not read characters or words. They read TOKENS -- pieces of words
produced by the model's own tokenizer. "unbelievable" might be three tokens;
a Chinese character might be two; a space is often part of the next token.

So chunking by character count is guesswork: 2,000 characters might be 400
tokens of English or 1,200 tokens of code, and the second one silently
overflows the model's window.

We use `tiktoken` with the `cl100k_base` encoding to convert text into the
actual token IDs, slice THOSE, then decode back to text. The result is chunks
that are genuinely the requested size.

WHAT MAKES THIS "PAGE-AWARE"
----------------------------
Before doing anything else, the text is split on the page markers that
extractor.py inserted. Each page is then chunked INDEPENDENTLY.

That matters for two reasons: no chunk ever straddles a page boundary and
becomes impossible to cite, and every chunk carries the page number it came
from -- which is exactly what the citation cards in the UI display.
"""

import re
from typing import Any, Dict, List

import tiktoken


class ChunkerService:
    """Splits extracted document text into embeddable chunks."""

    # ------------------------------------------------------------------
    # THE TOKENIZER, loaded once for the whole process.
    # ------------------------------------------------------------------
    # `cl100k_base` is the encoding used by OpenAI's text-embedding-3 models
    # and GPT-3.5/4. We use it as a consistent yardstick for "how big is this
    # piece of text", even when embedding with a different model -- what
    # matters is that the same ruler is used everywhere.
    #
    # This is a class attribute, so the tokenizer is built once when the module
    # is first imported rather than on every call.
    _encoder = tiktoken.get_encoding("cl100k_base")

    # Matches the page markers inserted by extractor.py. Two shapes exist:
    #     --- PAGE BREAK ---        (from normal PDF text extraction)
    #     --- PAGE 3 (OCR) ---      (from the OCR path)
    # Compiled once here rather than recompiled on every call.
    _page_pattern = re.compile(
        r"(?:\n|^)--- PAGE (?:BREAK|\d+ \((?:OCR|ocr)\)) ---\n?"
    )

    @classmethod
    def count_tokens(cls, text: str) -> int:
        """
        How many tokens is this text?

        `encode` turns text into a list of integer token ids, so the length of
        that list is the token count.
        """
        return len(cls._encoder.encode(text))

    # ==================================================================
    # PUBLIC ENTRY POINT
    # ==================================================================

    @classmethod
    def chunk_document(
        cls,
        text: str,
        strategy: str = "fixed_size",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Split a document's full text into chunks.

        Arguments:
            text          -- everything extractor.py produced, page markers included.
            strategy      -- "fixed_size" | "sentence" | "markdown".
            chunk_size    -- target size of each chunk, in TOKENS.
            chunk_overlap -- tokens repeated from the end of the previous chunk.

        Returns a list of dictionaries, each shaped like:
            {
              "content": "the text of this chunk",
              "chunk_index": 0,        # position within the whole document
              "page_number": 1,        # which page it came from
              "meta_data": {"token_count": 412, "character_count": 1789},
            }
        """
        # ---- Split on page boundaries FIRST ----
        # This is what makes every chunk citable to a specific page.
        parts = cls._page_pattern.split(text)
        # Drop empty fragments left behind by the split, and trim whitespace.
        parts = [part.strip() for part in parts if part.strip()]

        if not parts:
            # Nothing to chunk -- an empty or whitespace-only document.
            return []

        strategy = strategy.lower()

        # Markdown is the exception: headers define the structure, and a header
        # section can legitimately run across a page break. So this strategy
        # deliberately works on the WHOLE document rather than page by page.
        if strategy == "markdown":
            return cls._markdown_chunking(text)

        all_chunks: List[Dict[str, Any]] = []
        # Runs continuously across pages, so chunk_index is unique document-wide.
        chunk_index = 0

        # `enumerate(parts, 1)` counts from 1 because humans number pages from 1.
        for page_number, page_text in enumerate(parts, 1):
            if strategy == "fixed_size":
                page_chunks = cls._fixed_size_chunking_single_page(
                    page_text, chunk_size, chunk_overlap, page_number, chunk_index
                )
            elif strategy == "sentence":
                page_chunks = cls._sentence_chunking_single_page(
                    page_text, chunk_size, chunk_overlap, page_number, chunk_index
                )
            else:
                # Fail loudly on a typo rather than silently using a default the
                # caller did not ask for.
                raise ValueError(f"Unsupported chunking strategy: {strategy}")

            all_chunks.extend(page_chunks)
            chunk_index += len(page_chunks)

        return all_chunks

    # ==================================================================
    # STRATEGY 1: FIXED SIZE  -- the general-purpose default
    # ==================================================================

    @classmethod
    def _fixed_size_chunking_single_page(
        cls,
        text: str,
        chunk_size: int,
        chunk_overlap: int,
        page_number: int,
        start_chunk_index: int,
    ) -> List[Dict[str, Any]]:
        """
        Slice the page into fixed token windows that overlap slightly.

        With chunk_size=500 and chunk_overlap=50, the windows are:

            chunk 0 : tokens   0 .. 500
            chunk 1 : tokens 450 .. 950     <- repeats 50 tokens from chunk 0
            chunk 2 : tokens 900 .. 1400    <- repeats 50 tokens from chunk 1

        THE OVERLAP IS THE POINT. Without it, a sentence unlucky enough to land
        on a boundary gets cut in half, and neither half means anything on its
        own -- so neither half will ever match a query about it. Repeating the
        tail of the previous chunk guarantees every sentence appears complete in
        at least one chunk.
        """
        # Convert the text to token ids ONCE, then work purely in token space.
        tokens = cls._encoder.encode(text)
        total_tokens = len(tokens)

        if total_tokens == 0:
            return []

        # How far to advance the window each time. Smaller step = more overlap.
        step = chunk_size - chunk_overlap
        if step <= 0:
            # Guard against a nonsensical configuration where overlap >= size,
            # which would make step zero or negative and loop forever.
            step = chunk_size

        chunks = []
        chunk_index = start_chunk_index

        for start in range(0, total_tokens, step):
            # Python slicing clamps automatically, so the final window being
            # shorter than chunk_size needs no special handling.
            window = tokens[start : start + chunk_size]
            # Decode back into readable text for storage and display.
            chunk_text = cls._encoder.decode(window)

            chunks.append(
                {
                    "content": chunk_text,
                    "chunk_index": chunk_index,
                    "page_number": page_number,
                    "meta_data": {
                        "token_count": len(window),
                        "character_count": len(chunk_text),
                    },
                }
            )
            chunk_index += 1

            # We have consumed the whole page. Stop here, otherwise the overlap
            # would generate one final chunk containing only repeated text.
            if start + chunk_size >= total_tokens:
                break

        return chunks

    # ==================================================================
    # STRATEGY 2: SENTENCE  -- never cuts mid-sentence
    # ==================================================================

    @classmethod
    def _sentence_chunking_single_page(
        cls,
        text: str,
        chunk_size: int,
        chunk_overlap: int,
        page_number: int,
        start_chunk_index: int,
    ) -> List[Dict[str, Any]]:
        """
        Pack whole sentences together until the token budget is reached.

        Better than fixed-size for prose, because chunk boundaries always land
        between sentences. Each chunk therefore reads as complete text, which
        both embeds more cleanly and looks far better in a citation card.
        """
        # Split after . ! or ? when followed by whitespace.
        # `(?<=[.!?])` is a LOOKBEHIND: it matches the position after one of
        # those characters without consuming it, so the punctuation stays
        # attached to the sentence it ends.
        sentences = re.split(r"(?<=[.!?])\s+", text)

        chunks = []
        chunk_index = start_chunk_index

        # The sentences accumulating into the chunk currently being built.
        current_sentences: List[str] = []
        current_tokens = 0

        def flush() -> None:
            """Emit the accumulated sentences as one finished chunk."""
            # `nonlocal` lets this inner function reassign variables belonging
            # to the enclosing function rather than creating new local ones.
            nonlocal chunk_index, current_sentences, current_tokens

            if not current_sentences:
                return

            chunk_text = " ".join(current_sentences)
            chunks.append(
                {
                    "content": chunk_text,
                    "chunk_index": chunk_index,
                    "page_number": page_number,
                    "meta_data": {
                        "token_count": current_tokens,
                        "character_count": len(chunk_text),
                    },
                }
            )
            chunk_index += 1

        for sentence in sentences:
            if not sentence.strip():
                continue

            sentence_tokens = cls.count_tokens(sentence)

            # EDGE CASE: a single sentence larger than the whole budget -- a
            # run-on paragraph, or a table flattened into one line. It cannot be
            # packed with anything else, so close whatever is open and let this
            # sentence stand alone as its own oversized chunk. Splitting it
            # would defeat the entire purpose of this strategy.
            if sentence_tokens > chunk_size:
                flush()
                current_sentences = []
                current_tokens = 0

                chunks.append(
                    {
                        "content": sentence,
                        "chunk_index": chunk_index,
                        "page_number": page_number,
                        "meta_data": {
                            "token_count": sentence_tokens,
                            "character_count": len(sentence),
                            # Flagged so it is obvious in the data why this one
                            # chunk exceeds the configured size.
                            "oversized_sentence": True,
                        },
                    }
                )
                chunk_index += 1
                continue

            # Adding this sentence would overflow the budget, so close the
            # current chunk first.
            if current_tokens + sentence_tokens > chunk_size:
                flush()

                # ---- Build the overlap for the next chunk ----
                # Walk BACKWARDS through the sentences just emitted, keeping as
                # many as fit inside the overlap budget. Those trailing
                # sentences are repeated at the start of the next chunk, so
                # ideas spanning the boundary survive intact.
                overlap_sentences: List[str] = []
                overlap_tokens = 0

                for previous in reversed(current_sentences):
                    previous_tokens = cls.count_tokens(previous)
                    if overlap_tokens + previous_tokens > chunk_overlap:
                        break
                    # `insert(0, ...)` because we are walking backwards but must
                    # restore the original reading order.
                    overlap_sentences.insert(0, previous)
                    overlap_tokens += previous_tokens

                current_sentences = overlap_sentences
                current_tokens = overlap_tokens

            current_sentences.append(sentence)
            current_tokens += sentence_tokens

        # Emit whatever is left over after the loop ends.
        flush()

        return chunks

    # ==================================================================
    # STRATEGY 3: MARKDOWN  -- split on headers
    # ==================================================================

    @classmethod
    def _markdown_chunking(
        cls,
        text: str,
        start_index: int = 0,
        initial_header: str = "Intro",
        page_number: int = 1,
        state: "Dict[str, Any] | None" = None,
    ) -> List[Dict[str, Any]]:
        """
        Split a markdown document at its headers, one chunk per section.

        For structured documents this beats both other strategies, because the
        author has already marked where the topics change. A section under
        "## Refund Policy" is exactly the unit a reader would want returned.

        Each chunk is prefixed with its header text, so the embedding captures
        what the section is ABOUT even when the body never restates it.

        The keyword arguments exist for StreamingChunker below, which feeds a
        long document in pieces: it continues the numbering, carries the
        header that was open at the end of the previous piece, and reads the
        header that is open at the end of this one back out of `state`.
        """
        # Page markers are irrelevant here -- header structure spans pages -- so
        # strip them and treat the document as one continuous flow.
        clean_text = cls._page_pattern.sub("\n", text)
        lines = clean_text.splitlines()

        chunks = []
        chunk_index = start_index

        # Text appearing before the first header still needs a home.
        current_header = initial_header
        current_block: List[str] = []

        # `^(#{1,6})\s+(.*)$` matches one to six '#' characters, whitespace,
        # then the header text. Group 2 is the title itself.
        header_pattern = re.compile(r"^(#{1,6})\s+(.*)$")

        def flush() -> None:
            """Emit the accumulated lines as one section chunk."""
            nonlocal chunk_index, current_block

            if not current_block:
                return

            chunk_text = f"Header: {current_header}\n" + "\n".join(current_block)
            chunks.append(
                {
                    "content": chunk_text,
                    "chunk_index": chunk_index,
                    # Markdown has no meaningful pagination (1), unless it was
                    # fed a page at a time from a PDF.
                    "page_number": page_number,
                    "meta_data": {
                        "token_count": cls.count_tokens(chunk_text),
                        "character_count": len(chunk_text),
                        # Stored separately so the UI can show which section a
                        # result came from.
                        "header_section": current_header,
                    },
                }
            )
            chunk_index += 1
            current_block = []

        for line in lines:
            match = header_pattern.match(line)
            if match:
                # A new header means the previous section is complete.
                flush()
                current_header = match.group(2).strip()

            current_block.append(line)

        # The final section, which has no following header to trigger a flush.
        flush()

        if state is not None:
            state["header"] = current_header
        return chunks


# ======================================================================
# STREAMING: chunk a document while it is still being read
# ======================================================================


class StreamingChunker:
    """
    Chunks a document one Segment at a time (see extractor.iter_segments).

    `ChunkerService.chunk_document` needs the WHOLE text in one string. This
    takes a page, or a batch of spreadsheet rows, chunks just that, and
    remembers only two small things between calls:

        next_index   so chunk_index keeps counting across the whole document
        header       the markdown section still open at the end of the last
                     piece, so a section continuing onto the next page keeps
                     its title

    A segment with page_number=None is a whole document whose text may carry
    its own page markers (a DOCX, a ZIP of PDFs); it goes through
    chunk_document exactly as before, renumbered to continue the count.

        chunker = StreamingChunker("fixed_size", 500, 50)
        for segment in DocumentExtractor.iter_segments(path, "pdf", info):
            for chunk in chunker.feed(segment.text, segment.page_number):
                save(chunk)
    """

    def __init__(self, strategy: str, chunk_size: int, chunk_overlap: int):
        self.strategy = strategy.lower()
        if self.strategy not in ("fixed_size", "sentence", "markdown"):
            raise ValueError(f"Unsupported chunking strategy: {strategy}")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.next_index = 0
        self._state: Dict[str, Any] = {"header": "Intro"}

    def feed(self, text: str, page_number: "int | None", extra_meta: "Dict[str, Any] | None" = None) -> List[Dict[str, Any]]:
        """The chunks of one segment, numbered after every chunk so far."""
        if page_number is None:
            chunks = ChunkerService.chunk_document(
                text, strategy=self.strategy, chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
            )
            for chunk in chunks:
                chunk["chunk_index"] += self.next_index
        else:
            text = text.strip()
            if not text:
                return []
            if self.strategy == "markdown":
                chunks = ChunkerService._markdown_chunking(
                    text,
                    start_index=self.next_index,
                    initial_header=self._state["header"],
                    page_number=page_number,
                    state=self._state,
                )
            elif self.strategy == "sentence":
                chunks = ChunkerService._sentence_chunking_single_page(
                    text, self.chunk_size, self.chunk_overlap, page_number, self.next_index
                )
            else:
                chunks = ChunkerService._fixed_size_chunking_single_page(
                    text, self.chunk_size, self.chunk_overlap, page_number, self.next_index
                )
        if extra_meta:
            for chunk in chunks:
                chunk["meta_data"].update(extra_meta)
        self.next_index += len(chunks)
        return chunks
