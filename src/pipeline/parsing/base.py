"""파싱 엔진 기반 타입.

FormatParser Protocol, ParseResult, ParseMetrics, DocumentProfile.
모든 확장자별 파서는 FormatParser를 구현한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class DocumentComplexity(str, Enum):
    """문서 복잡도 등급. PDF 라우팅 판단 기준."""
    TEXT_ONLY = "text_only"       # 순수 텍스트 (PyMuPDF로 충분)
    TEXT_WITH_TABLES = "text_tables"  # 텍스트 + 표 (Docling 권장)
    MIXED = "mixed"              # 텍스트 + 이미지 혼합 (Docling + OCR)
    IMAGE_HEAVY = "image_heavy"  # 이미지 위주/스캔 (VLM OCR 필수)


@dataclass(frozen=True)
class DocumentProfile:
    """PyMuPDF 사전 분석으로 생성되는 문서 프로파일.

    PDF 내부 메타데이터를 빠르게 스캔하여
    어떤 파서에 넣어야 최적인지 판단하는 근거 데이터.
    """
    total_pages: int = 0
    text_pages: int = 0          # 텍스트 추출 가능한 페이지 수
    image_pages: int = 0         # 이미지가 포함된 페이지 수
    image_only_pages: int = 0    # 텍스트 없이 이미지만 있는 페이지 수
    total_chars: int = 0         # 전체 추출된 문자 수
    total_images: int = 0        # 전체 이미지 객체 수
    has_tables: bool = False     # 표 구조 감지 여부
    avg_chars_per_page: float = 0.0
    image_area_ratio: float = 0.0  # 이미지 면적 / 전체 페이지 면적 비율 (0~1)
    complexity: DocumentComplexity = DocumentComplexity.TEXT_ONLY
    recommended_parser: str = "pymupdf"  # pymupdf | docling | vlm


@dataclass(frozen=True)
class ParseMetrics:
    """파싱 성능/품질 메트릭.

    모든 파서가 반환하는 정량적 측정치.
    파싱 품질 추적 및 파서 간 비교에 사용.
    """
    # 성능 지표
    parse_time_ms: float = 0.0       # 파싱 소요 시간 (ms)
    total_chars: int = 0             # 출력 마크다운 총 문자 수
    total_pages: int = 0             # 처리된 페이지 수

    # 구조 보존 지표
    heading_count: int = 0           # 마크다운 헤딩 수 (# ~ ######)
    table_count: int = 0             # 마크다운 테이블 수
    list_count: int = 0              # 마크다운 리스트 수
    code_block_count: int = 0        # 코드 블록 수
    image_ref_count: int = 0         # 이미지 참조 수

    # 품질 지표
    empty_line_ratio: float = 0.0    # 빈 줄 비율 (높으면 파싱 실패 의심)
    avg_line_length: float = 0.0     # 평균 줄 길이 (비정상적으로 짧으면 이상)
    parser_used: str = ""            # 실제 사용된 파서 이름
    parser_reason: str = ""          # 파서 선택 이유

    # PDF 전용
    document_profile: DocumentProfile | None = None


@dataclass(frozen=True)
class ParseResult:
    """파싱 결과. 마크다운 텍스트 + 메트릭."""
    markdown: str
    metrics: ParseMetrics
    source_mime_type: str = ""
    source_file_name: str = ""


@runtime_checkable
class FormatParser(Protocol):
    """확장자별 파서 프로토콜.

    모든 파서는 이 인터페이스를 구현한다.
    file_bytes → ParseResult (markdown + metrics).
    """

    async def parse(self, file_bytes: bytes, file_name: str = "") -> ParseResult:
        """파일 바이트를 마크다운으로 변환한다."""
        ...

    def supported_mime_types(self) -> list[str]:
        """지원하는 MIME 타입 목록."""
        ...
