"""PDF 파서.

자동 감지 라우팅으로 최적 변환율을 추구한다:

1. PyMuPDF 사전 분석 → DocumentProfile (문서 프로파일링)
2. 복잡도에 따라 파서 자동 선택:
   - TEXT_ONLY → PyMuPDF 직접 추출 (빠르고 정확, API 호출 불필요)
   - TEXT_WITH_TABLES / MIXED / IMAGE_HEAVY → DocForge API 위임

의존성: PyMuPDF(필수), DocForgeClient(선택)
"""

from __future__ import annotations

import time

from src.observability.logging import get_logger
from src.pipeline.parsing.base import (
    DocumentComplexity,
    ParseMetrics,
    ParseResult,
)
from src.pipeline.parsing.docforge_client import DocForgeClient, ParseError
from src.pipeline.parsing.metrics import compute_markdown_metrics
from src.pipeline.parsing.pdf_analyzer import analyze_pdf

logger = get_logger(__name__)

_SUPPORTED = ["application/pdf"]


class PdfParser:
    """PDF → Markdown 파서. 자동 감지 라우팅."""

    def __init__(
        self,
        docforge_client: DocForgeClient | None = None,
        fallback_enabled: bool = False,
    ):
        self._docforge_client = docforge_client
        self._fallback_enabled = fallback_enabled

    async def parse(self, file_bytes: bytes, file_name: str = "") -> ParseResult:
        """PDF를 분석하고 최적 파서로 마크다운 변환한다."""
        import asyncio

        t0 = time.time()

        # 1단계: PyMuPDF 사전 분석 — CPU 바운드이므로 executor 위임
        loop = asyncio.get_running_loop()
        profile = await loop.run_in_executor(None, analyze_pdf, file_bytes)

        # 2단계: 복잡도에 따라 파서 라우팅
        recommended = profile.recommended_parser
        parser_reason = f"complexity={profile.complexity.value}"

        if recommended == "pymupdf":
            # TEXT_ONLY → 로컬 PyMuPDF 직접 추출
            markdown = self._parse_with_pymupdf(file_bytes)
            parser_used = "pymupdf"
            parser_reason += ", text_only → PyMuPDF 직접 추출"

        elif recommended == "docforge" and self._docforge_client is not None:
            # TEXT_WITH_TABLES / MIXED / IMAGE_HEAVY → DocForge 위임
            try:
                docforge_result = await self._docforge_client.parse(
                    file_bytes=file_bytes,
                    file_name=file_name,
                    mime_type="application/pdf",
                )
                markdown = docforge_result.markdown
                parser_used = "docforge"
                parser_reason += f", {profile.complexity.value} → DocForge 위임"
            except ParseError:
                if self._fallback_enabled:
                    logger.warning(
                        "docforge_unavailable_fallback",
                        file_name=file_name,
                        complexity=profile.complexity.value,
                    )
                    markdown = self._parse_with_pymupdf(file_bytes)
                    parser_used = "pymupdf"
                    parser_reason += ", DocForge 실패 → PyMuPDF 폴백"
                else:
                    raise

        elif recommended == "docforge" and self._docforge_client is None:
            # DocForge 미설정 → PyMuPDF 폴백
            markdown = self._parse_with_pymupdf(file_bytes)
            parser_used = "pymupdf"
            parser_reason += ", DocForge 미설정 → PyMuPDF 폴백"

        else:
            # 기본 폴백
            markdown = self._parse_with_pymupdf(file_bytes)
            parser_used = "pymupdf"
            parser_reason += ", default fallback"

        elapsed_ms = (time.time() - t0) * 1000

        # 3단계: 메트릭 수집
        metrics = compute_markdown_metrics(
            markdown=markdown,
            parse_time_ms=elapsed_ms,
            total_pages=profile.total_pages,
            parser_used=parser_used,
            parser_reason=parser_reason,
            document_profile=profile,
        )

        logger.info(
            "pdf_parsed",
            file_name=file_name,
            parser=parser_used,
            pages=profile.total_pages,
            chars=metrics.total_chars,
            tables=metrics.table_count,
            latency_ms=round(elapsed_ms, 1),
            complexity=profile.complexity.value,
        )

        return ParseResult(
            markdown=markdown,
            metrics=metrics,
            source_mime_type="application/pdf",
            source_file_name=file_name,
        )

    def supported_mime_types(self) -> list[str]:
        return list(_SUPPORTED)

    # --- 개별 파서 구현 ---

    @staticmethod
    def _parse_with_pymupdf(file_bytes: bytes) -> str:
        """PyMuPDF로 텍스트 직접 추출. 가장 빠르고 정확 (네이티브 텍스트 PDF).

        AI 파서에 넣으면 멀쩡한 글자가 변형될 수 있으므로
        텍스트만 있는 PDF는 반드시 이 경로를 사용한다.
        """
        try:
            import fitz
        except ImportError:
            raise ImportError("PyMuPDF is required: pip install PyMuPDF")

        pages = []
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            for page in doc:
                text = page.get_text("text")
                if text.strip():
                    pages.append(text.strip())

        return "\n\n".join(pages)
