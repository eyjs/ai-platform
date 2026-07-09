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
    """Markdown 헤딩 기반 청커 + 섹션 계층(AST-lite) 추적.

    파싱 결과(markdown)의 헤딩 트리를 청킹 시점에 복원해 각 청크의
    `metadata.section_path`(루트→현재 섹션 경로)와 `section_level`로 남긴다.
    이 메타데이터가 "청크 → 원본 문서의 어느 계층인지" 복원의 근거가 된다 —
    flat 이웃확장(chunk_index)만 있던 원본복원축을 계층 기반으로 격상하는 토대.
    """

    _HEADING_RE = None  # 클래스 로드 시 1회 컴파일 (아래 __init__)

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        import re

        self._chunk_size = chunk_size
        self._text_chunker = TextChunker(chunk_size, chunk_overlap)
        self._encoding = tiktoken.get_encoding("cl100k_base")
        if MarkdownChunker._HEADING_RE is None:
            MarkdownChunker._HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

    def split(self, text: str) -> List[dict]:
        import re

        sections = re.split(r'(?=^#{1,6}\s)', text, flags=re.MULTILINE)
        chunks = []
        chunk_index = 0
        # 헤딩 스택: [(level, title), ...] — 현재 위치의 조상 헤딩 경로.
        heading_stack: list[tuple[int, str]] = []

        for section in sections:
            section = section.strip()
            if not section:
                continue

            # 섹션 선두 헤딩으로 스택 갱신 (같거나 얕은 레벨은 pop 후 push).
            m = self._HEADING_RE.match(section)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))

            metadata = {}
            if heading_stack:
                metadata = {
                    "section_path": [t for _, t in heading_stack],
                    "section_level": heading_stack[-1][0],
                }

            if len(section) <= self._chunk_size:
                chunks.append({
                    "chunkIndex": chunk_index,
                    "content": section,
                    "tokenCount": len(self._encoding.encode(section)),
                    "metadata": dict(metadata),
                })
                chunk_index += 1
            else:
                sub_chunks = self._text_chunker.split(section)
                for part_no, sc in enumerate(sub_chunks):
                    sc["chunkIndex"] = chunk_index
                    # 대형 섹션의 분할 청크도 같은 섹션 경로를 상속(부분 번호 표기).
                    sc["metadata"] = {**metadata, "section_part": part_no} if metadata else {}
                    chunks.append(sc)
                    chunk_index += 1

        return chunks
