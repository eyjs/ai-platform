"""파싱 엔진 기반 타입.

ParseResult, ParseMetrics.
모든 파싱은 DocForge에 위임하고 결과만 수신한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ParseMetrics:
    """파싱 성능/품질 메트릭.

    모든 파서가 반환하는 정량적 측정치.
    파싱 품질 추적 및 파서 간 비교에 사용.
    """
    parse_time_ms: float = 0.0
    total_chars: int = 0
    total_pages: int = 0

    heading_count: int = 0
    table_count: int = 0
    list_count: int = 0
    code_block_count: int = 0
    image_ref_count: int = 0

    empty_line_ratio: float = 0.0
    avg_line_length: float = 0.0
    parser_used: str = ""
    parser_reason: str = ""


@dataclass(frozen=True)
class ParseResult:
    """파싱 결과. 마크다운 텍스트 + 메트릭."""
    markdown: str
    metrics: ParseMetrics
    source_mime_type: str = ""
    source_file_name: str = ""


@runtime_checkable
class FormatParser(Protocol):
    """확장자별 파서 프로토콜."""

    async def parse(self, file_bytes: bytes, file_name: str = "") -> ParseResult:
        """파일 바이트를 마크다운으로 변환한다."""
        ...

    def supported_mime_types(self) -> list[str]:
        """지원하는 MIME 타입 목록."""
        ...
