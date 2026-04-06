"""PDF 파서.

자동 감지 라우팅으로 최적 변환율을 추구한다:

1. PyMuPDF 사전 분석 → DocumentProfile (문서 프로파일링)
2. 복잡도에 따라 파서 자동 선택:
   - TEXT_ONLY → PyMuPDF 직접 추출 (AI 파서 불필요, 글자 변형 방지)
   - TEXT_WITH_TABLES → Docling (TableFormer로 표 구조 완벽 보존)
   - MIXED → Docling (레이아웃 + 표 + OCR 통합)
   - IMAGE_HEAVY → VLM OCR (PaddleOCR-VL/외부 API)

의존성: PyMuPDF(필수), Docling(선택), VLM(선택)
"""

from __future__ import annotations

import io
import re
import time

from src.observability.logging import get_logger
from src.pipeline.parsing.base import (
    DocumentComplexity,
    FormatParser,
    ParseMetrics,
    ParseResult,
)
from src.pipeline.parsing.pdf_analyzer import analyze_pdf
from src.pipeline.parsing.metrics import compute_markdown_metrics

logger = get_logger(__name__)

_SUPPORTED = ["application/pdf"]


class PdfParser:
    """PDF → Markdown 파서. 자동 감지 라우팅."""

    def __init__(
        self,
        enable_docling: bool = True,
        enable_vlm: bool = False,
        vlm_endpoint: str = "",
    ):
        self._enable_docling = enable_docling
        self._enable_vlm = enable_vlm
        self._vlm_endpoint = vlm_endpoint

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
            markdown = self._parse_with_pymupdf(file_bytes)
            parser_used = "pymupdf"
            parser_reason += ", text_only → PyMuPDF 직접 추출 (AI 파서 불필요)"

        elif recommended == "docling" and self._enable_docling:
            markdown = await self._parse_with_docling(file_bytes)
            parser_used = "docling"
            parser_reason += ", 표/레이아웃 → Docling (TableFormer)"

        elif recommended == "vlm" and self._enable_vlm:
            markdown = await self._parse_with_vlm(file_bytes)
            parser_used = "vlm"
            parser_reason += ", 이미지 위주 → VLM OCR"

        elif recommended == "docling" and not self._enable_docling:
            # Docling 비활성 → PyMuPDF 폴백
            markdown = self._parse_with_pymupdf(file_bytes)
            parser_used = "pymupdf"
            parser_reason += ", docling 비활성 → PyMuPDF 폴백"

        elif recommended == "vlm" and not self._enable_vlm:
            # VLM 비활성 → Docling 또는 PyMuPDF 폴백
            if self._enable_docling:
                markdown = await self._parse_with_docling(file_bytes)
                parser_used = "docling"
                parser_reason += ", vlm 비활성 → Docling 폴백"
            else:
                markdown = self._parse_with_pymupdf(file_bytes)
                parser_used = "pymupdf"
                parser_reason += ", vlm+docling 비활성 → PyMuPDF 폴백"

        else:
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

    @staticmethod
    async def _parse_with_docling(file_bytes: bytes) -> str:
        """Docling으로 레이아웃+표 보존 파싱.

        DocLayNet(레이아웃 감지) + TableFormer(표 구조 복원)를 결합하여
        복잡한 표, 병합 셀, 다단 레이아웃을 완벽한 마크다운으로 변환한다.
        """
        try:
            from docling.document_converter import DocumentConverter
        except ImportError:
            raise ImportError("Docling is required: pip install docling")

        import asyncio
        import tempfile
        from pathlib import Path

        # Docling은 파일 경로를 받으므로 임시 파일 생성
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            converter = DocumentConverter()
            # Docling은 동기 API이므로 executor에서 실행
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: converter.convert(Path(tmp_path)).document,
            )
            markdown = result.export_to_markdown()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        return markdown

    async def _parse_with_vlm(self, file_bytes: bytes) -> str:
        """VLM OCR로 이미지 기반 문서 파싱.

        PaddleOCR-VL, olmOCR 등 VLM 서버에 이미지를 전송하여
        스캔 문서/이미지 PDF에서 구조화된 마크다운을 추출한다.

        현재: 외부 HTTP API 호출 구조 (vLLM 서버).
        향후: 로컬 모델 직접 로드 지원.
        """
        if not self._vlm_endpoint:
            raise RuntimeError(
                "VLM endpoint not configured. "
                "Set AIP_VLM_OCR_ENDPOINT for image-heavy PDF parsing."
            )

        import httpx

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._vlm_endpoint}/parse",
                files={"file": ("document.pdf", file_bytes, "application/pdf")},
                data={"output_format": "markdown"},
            )
            resp.raise_for_status()
            return resp.json().get("markdown", "")
