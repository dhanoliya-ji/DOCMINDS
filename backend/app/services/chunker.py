"""It cuts a long document into small, self-contained pieces called CHUNKS."""
import re
from typing import Any, Dict, List

import tiktoken


class ChunkerService:
    """Splits extracted document text into embeddable chunks."""

    _encoder = tiktoken.get_encoding("cl100k_base")

    _page_pattern = re.compile(
        r"(?:\n|^)--- PAGE (?:BREAK|\d+ \((?:OCR|ocr)\)) ---\n?"
    )

    @classmethod
    def count_tokens(cls, text: str) -> int:
        """How many tokens is this text?"""
        return len(cls._encoder.encode(text))


    @classmethod
    def chunk_document(
        cls,
        text: str,
        strategy: str = "fixed_size",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> List[Dict[str, Any]]:
        """Split a document's full text into chunks."""
        parts = cls._page_pattern.split(text)
        parts = [part.strip() for part in parts if part.strip()]

        if not parts:
            return []

        strategy = strategy.lower()

        if strategy == "markdown":
            return cls._markdown_chunking(text)

        all_chunks: List[Dict[str, Any]] = []
        chunk_index = 0

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
                raise ValueError(f"Unsupported chunking strategy: {strategy}")

            all_chunks.extend(page_chunks)
            chunk_index += len(page_chunks)

        return all_chunks


    @classmethod
    def _fixed_size_chunking_single_page(
        cls,
        text: str,
        chunk_size: int,
        chunk_overlap: int,
        page_number: int,
        start_chunk_index: int,
    ) -> List[Dict[str, Any]]:
        """Slice the page into fixed token windows that overlap slightly."""
        tokens = cls._encoder.encode(text)
        total_tokens = len(tokens)

        if total_tokens == 0:
            return []

        step = chunk_size - chunk_overlap
        if step <= 0:
            step = chunk_size

        chunks = []
        chunk_index = start_chunk_index

        for start in range(0, total_tokens, step):
            window = tokens[start : start + chunk_size]
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

            if start + chunk_size >= total_tokens:
                break

        return chunks


    @classmethod
    def _sentence_chunking_single_page(
        cls,
        text: str,
        chunk_size: int,
        chunk_overlap: int,
        page_number: int,
        start_chunk_index: int,
    ) -> List[Dict[str, Any]]:
        """Pack whole sentences together until the token budget is reached."""
        sentences = re.split(r"(?<=[.!?])\s+", text)

        chunks = []
        chunk_index = start_chunk_index

        current_sentences: List[str] = []
        current_tokens = 0

        def flush() -> None:
            """Emit the accumulated sentences as one finished chunk."""
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
                            "oversized_sentence": True,
                        },
                    }
                )
                chunk_index += 1
                continue

            if current_tokens + sentence_tokens > chunk_size:
                flush()

                overlap_sentences: List[str] = []
                overlap_tokens = 0

                for previous in reversed(current_sentences):
                    previous_tokens = cls.count_tokens(previous)
                    if overlap_tokens + previous_tokens > chunk_overlap:
                        break
                    overlap_sentences.insert(0, previous)
                    overlap_tokens += previous_tokens

                current_sentences = overlap_sentences
                current_tokens = overlap_tokens

            current_sentences.append(sentence)
            current_tokens += sentence_tokens

        flush()

        return chunks


    @classmethod
    def _markdown_chunking(
        cls,
        text: str,
        start_index: int = 0,
        initial_header: str = "Intro",
        page_number: int = 1,
        state: "Dict[str, Any] | None" = None,
    ) -> List[Dict[str, Any]]:
        """Split a markdown document at its headers, one chunk per section."""
        clean_text = cls._page_pattern.sub("\n", text)
        lines = clean_text.splitlines()

        chunks = []
        chunk_index = start_index

        current_header = initial_header
        current_block: List[str] = []

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
                    "page_number": page_number,
                    "meta_data": {
                        "token_count": cls.count_tokens(chunk_text),
                        "character_count": len(chunk_text),
                        "header_section": current_header,
                    },
                }
            )
            chunk_index += 1
            current_block = []

        for line in lines:
            match = header_pattern.match(line)
            if match:
                flush()
                current_header = match.group(2).strip()

            current_block.append(line)

        flush()

        if state is not None:
            state["header"] = current_header
        return chunks


class StreamingChunker:
    """Chunks a document one Segment at a time (see extractor.iter_segments)."""

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
