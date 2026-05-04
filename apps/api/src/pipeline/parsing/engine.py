"""통합 파싱 엔진.

모든 파일은 이 엔진을 통해 진입한다.
확장자 → MIME 타입 → 전용 파서 라우팅 → ParseResult (markdown + metrics).

허용 확장자: pdf, csv, xlsx/xls
PDF의 TEXT_ONLY는 로컬 PyMuPDF, 나머지는 DocForge API로 위임.
CSV/Excel은 DocForge API로 직접 위임.

사용법:
    engine = ParsingEngine(
        docforge_url="http://localhost:5001",
        docforge_timeout_sec=120.0,
    )
    result = await engine.parse(file_bytes, "계약서.pdf")
"""

from __future__ import annotations

import os
import time

from src.observability.logging import get_logger
from src.pipeline.parsing.base import ParseMetrics, ParseResult
from src.pipeline.parsing.docforge_client import DocForgeClient, ParseError
from src.pipeline.parsing.metrics import compute_markdown_metrics
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

# DocForge로 직접 위임하는 MIME 타입 (CSV, Excel)
_DOCFORGE_DIRECT_MIMES: set[str] = {
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}


class ParsingEngine:
    """통합 파싱 엔진.

    진입점이 하나이고, 확장자로 내부 파서를 라우팅한다.
    PDF의 TEXT_ONLY는 로컬 PyMuPDF, 나머지는 DocForge API로 위임.
    CSV/Excel은 DocForge API로 직접 위임.
    """

    def __init__(
        self,
        docforge_url: str = "http://localhost:5001",
        docforge_timeout_sec: float = 120.0,
        docforge_fallback_enabled: bool = False,
    ):
        self._docforge_client = DocForgeClient(
            base_url=docforge_url,
            timeout_sec=docforge_timeout_sec,
        )
        self._pdf_parser = PdfParser(
            docforge_client=self._docforge_client,
            fallback_enabled=docforge_fallback_enabled,
        )

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

        logger.info(
            "parsing_engine_dispatch",
            file_name=file_name,
            extension=ext,
            mime_type=mime_type,
        )

        # PDF → PdfParser (TEXT_ONLY는 로컬, 나머지는 DocForge)
        if mime_type == "application/pdf":
            return await self._pdf_parser.parse(file_bytes, file_name)

        # CSV/Excel → DocForge 직접 위임
        if mime_type in _DOCFORGE_DIRECT_MIMES:
            return await self._parse_with_docforge(file_bytes, file_name, mime_type)

        # Unreachable (모든 MIME이 위에서 처리됨)
        raise UnsupportedFormatError(
            extension=ext.lstrip("."),
            allowed=ALLOWED_EXTENSIONS,
        )

    async def _parse_with_docforge(
        self,
        file_bytes: bytes,
        file_name: str,
        mime_type: str,
    ) -> ParseResult:
        """DocForge API로 파일을 직접 위임한다 (CSV/Excel)."""
        t0 = time.time()

        docforge_result = await self._docforge_client.parse(
            file_bytes=file_bytes,
            file_name=file_name,
            mime_type=mime_type,
        )

        elapsed_ms = (time.time() - t0) * 1000

        # 파서 유형 결정 (로깅/메트릭용)
        parser_used = "docforge"
        if "text/csv" in mime_type:
            parser_used = "docforge_csv"
        elif "spreadsheet" in mime_type or "ms-excel" in mime_type:
            parser_used = "docforge_excel"

        metrics = compute_markdown_metrics(
            markdown=docforge_result.markdown,
            parse_time_ms=elapsed_ms,
            total_pages=1,
            parser_used=parser_used,
            parser_reason=f"mime={mime_type}, docforge_delegated",
        )

        return ParseResult(
            markdown=docforge_result.markdown,
            metrics=metrics,
            source_mime_type=mime_type,
            source_file_name=file_name,
        )

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
