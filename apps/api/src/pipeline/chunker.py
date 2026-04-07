"""텍스트 청커: RecursiveCharacterTextSplitter 스타일."""

import logging
from typing import List

import tiktoken

logger = logging.getLogger(__name__)

SEPARATORS = ["\n\n", "\n", ". ", " "]


class TextChunker:
    """RecursiveCharacterTextSplitter 스타일 청커."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def split(self, text: str) -> List[dict]:
        chunks = self._recursive_split(text, SEPARATORS)
        return [
            {
                "chunkIndex": i,
                "content": chunk,
                "tokenCount": len(self._encoding.encode(chunk)),
            }
            for i, chunk in enumerate(chunks)
            if chunk.strip()
        ]

    def _recursive_split(self, text: str, separators: list[str]) -> list[str]:
        if len(text) <= self._chunk_size:
            return [text]

        sep = separators[0] if separators else ""
        remaining_seps = separators[1:] if len(separators) > 1 else []

        if sep and sep in text:
            parts = text.split(sep)
        else:
            if remaining_seps:
                return self._recursive_split(text, remaining_seps)
            return self._force_split(text)

        chunks = []
        current = ""

        for part in parts:
            candidate = (current + sep + part) if current else part
            if len(candidate) <= self._chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                if len(part) > self._chunk_size and remaining_seps:
                    chunks.extend(self._recursive_split(part, remaining_seps))
                elif len(part) > self._chunk_size:
                    chunks.extend(self._force_split(part))
                else:
                    current = part
                    continue
                current = ""

        if current:
            chunks.append(current)

        if self._chunk_overlap > 0 and len(chunks) > 1:
            chunks = self._apply_overlap(chunks)

        return chunks

    def _force_split(self, text: str) -> list[str]:
        chunks = []
        for i in range(0, len(text), self._chunk_size - self._chunk_overlap):
            chunks.append(text[i : i + self._chunk_size])
        return chunks

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        result = [chunks[0]]
        for i in range(1, len(chunks)):
            overlap = chunks[i - 1][-self._chunk_overlap :]
            result.append(overlap + chunks[i])
        return result


class MarkdownChunker:
    """Markdown 헤딩 기반 청커."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self._chunk_size = chunk_size
        self._text_chunker = TextChunker(chunk_size, chunk_overlap)
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def split(self, text: str) -> List[dict]:
        import re

        sections = re.split(r'(?=^#{1,6}\s)', text, flags=re.MULTILINE)
        chunks = []
        chunk_index = 0

        for section in sections:
            section = section.strip()
            if not section:
                continue

            if len(section) <= self._chunk_size:
                chunks.append({
                    "chunkIndex": chunk_index,
                    "content": section,
                    "tokenCount": len(self._encoding.encode(section)),
                })
                chunk_index += 1
            else:
                sub_chunks = self._text_chunker.split(section)
                for sc in sub_chunks:
                    sc["chunkIndex"] = chunk_index
                    chunks.append(sc)
                    chunk_index += 1

        return chunks
