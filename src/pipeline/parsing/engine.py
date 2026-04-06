"""통합 파싱 엔진.

모든 파일은 이 엔진을 통해 진입한다.
확장자 → MIME 타입 → 전용 파서 라우팅 → ParseResult (markdown + metrics).

허용 확장자: pdf, csv, xlsx/xls
그 외 확장자는 UnsupportedFormatError를 던져 프론트에서 안내한다.

사용법:
    engine = ParsingEngine(...)
    result = await engine.parse(file_bytes, "계약서.pdf")
    # result.markdown → 마크다운 텍스트
    # result.metrics  → 파싱 성능/품질 메트릭
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.observability.logging import get_logger
from src.pipeline.parsing.base import ParseResult
from src.pipeline.parsing.csv_parser import CsvParser
from src.pipeline.parsing.excel_parser import ExcelParser
from src.pipeline.parsing.pdf_parser import PdfParser

logger = get_logger(__name__)


class UnsupportedFormatError(ValueError):
    """허용되지 않는 파일 확장자. 프론트에 deny 응답으로 전달된다."""

    def __init__(self, extension: str, allowed: list[str]):
        self.extension = extension
        self.allowed = allowed
        allowed_str = ", ".join(allowed)
        super().__init__(
            f"지원하지 않는 파일 형식입니다: .{extension} — "
            f"허용 확장자: {allowed_str}"
        )


# 확장자 → MIME 타입 매핑
_EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
}

# 허용 확장자 목록 (프론트 안내용)
ALLOWED_EXTENSIONS: list[str] = ["pdf", "csv", "xlsx", "xls"]


class ParsingEngine:
    """통합 파싱 엔진.

    진입점이 하나이고, 확장자로 내부 파서를 라우팅한다.
    각 확장자별 파서는 독립적으로 고도화할 수 있다.
    """

    def __init__(
        self,
        enable_docling: bool = True,
        enable_vlm: bool = False,
        vlm_endpoint: str = "",
        csv_max_rows: int = 10000,
        excel_max_rows_per_sheet: int = 10000,
    ):
        self._pdf_parser = PdfParser(
            enable_docling=enable_docling,
            enable_vlm=enable_vlm,
            vlm_endpoint=vlm_endpoint,
        )
        self._csv_parser = CsvParser(max_rows=csv_max_rows)
        self._excel_parser = ExcelParser(max_rows_per_sheet=excel_max_rows_per_sheet)

        # MIME → 파서 매핑
        self._parsers: dict[str, object] = {
            "application/pdf": self._pdf_parser,
            "text/csv": self._csv_parser,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": self._excel_parser,
            "application/vnd.ms-excel": self._excel_parser,
        }

    async def parse(self, file_bytes: bytes, file_name: str) -> ParseResult:
        """파일을 파싱하여 마크다운으로 변환한다.

        Args:
            file_bytes: 파일 바이너리 데이터.
            file_name: 파일명 (확장자로 파서 결정).

        Returns:
            ParseResult: 마크다운 + 메트릭.

        Raises:
            UnsupportedFormatError: 허용되지 않는 확장자.
            ValueError: 파일명 없음 또는 빈 파일.
        """
        if not file_name:
            raise ValueError("file_name은 필수입니다. 확장자로 파서를 결정합니다.")

        if not file_bytes:
            raise ValueError("빈 파일입니다.")

        # 확장자 추출 + 라우팅
        ext = _extract_extension(file_name)
        mime_type = _EXT_TO_MIME.get(ext)

        if mime_type is None:
            raise UnsupportedFormatError(
                extension=ext.lstrip("."),
                allowed=ALLOWED_EXTENSIONS,
            )

        parser = self._parsers[mime_type]

        logger.info(
            "parsing_engine_dispatch",
            file_name=file_name,
            extension=ext,
            mime_type=mime_type,
            parser=type(parser).__name__,
        )

        result = await parser.parse(file_bytes, file_name)
        return result

    def is_supported(self, file_name: str) -> bool:
        """파일명의 확장자가 지원되는지 확인한다."""
        ext = _extract_extension(file_name)
        return ext in _EXT_TO_MIME

    @staticmethod
    def allowed_extensions() -> list[str]:
        """허용 확장자 목록. 프론트 안내용."""
        return list(ALLOWED_EXTENSIONS)


def _extract_extension(file_name: str) -> str:
    """파일명에서 확장자를 추출한다 (.pdf, .csv 등)."""
    _, ext = os.path.splitext(file_name)
    return ext.lower()
