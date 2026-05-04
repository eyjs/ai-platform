"""통합 파싱 엔진.

모든 파일은 이 엔진을 통해 진입한다.
확장자 → MIME 타입 → DocForge API 위임 → ParseResult (markdown + metrics).

파싱 전략(복잡도 분석, OCR, 표 추출 등)은 전부 DocForge 책임.
ai-platform은 파일을 넘기고 마크다운만 받아온다.

허용 확장자: pdf, csv, xlsx/xls

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
from src.pipeline.parsing.base import ParseResult
from src.pipeline.parsing.docforge_client import DocForgeClient
from src.pipeline.parsing.metrics import compute_markdown_metrics

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


_EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
}

ALLOWED_EXTENSIONS: list[str] = ["pdf", "csv", "xlsx", "xls"]

_MIME_LABEL: dict[str, str] = {
    "application/pdf": "docforge_pdf",
    "text/csv": "docforge_csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "docforge_excel",
    "application/vnd.ms-excel": "docforge_excel",
}


class ParsingEngine:
    """통합 파싱 엔진.

    모든 파일을 DocForge API에 위임하고 마크다운 결과를 수신한다.
    파싱 전략은 DocForge가 결정한다.
    """

    def __init__(
        self,
        docforge_url: str = "http://localhost:5001",
        docforge_timeout_sec: float = 120.0,
        docforge_internal_key: str = "",
    ):
        self._docforge_client = DocForgeClient(
            base_url=docforge_url,
            timeout_sec=docforge_timeout_sec,
            internal_key=docforge_internal_key,
        )

    async def parse(self, file_bytes: bytes, file_name: str) -> ParseResult:
        """파일을 DocForge에 위임하여 마크다운으로 변환한다."""
        if not file_name:
            raise ValueError("file_name은 필수입니다. 확장자로 파서를 결정합니다.")

        if not file_bytes:
            raise ValueError("빈 파일입니다.")

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

        t0 = time.time()

        docforge_result = await self._docforge_client.parse(
            file_bytes=file_bytes,
            file_name=file_name,
            mime_type=mime_type,
        )

        elapsed_ms = (time.time() - t0) * 1000
        parser_label = _MIME_LABEL.get(mime_type, "docforge")

        metrics = compute_markdown_metrics(
            markdown=docforge_result.markdown,
            parse_time_ms=elapsed_ms,
            total_pages=docforge_result.metadata.get("total_pages", 1),
            parser_used=parser_label,
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
