"""통합 파싱 엔진 테스트.

ParsingEngine 라우팅, 확장자 감지, deny, PDF 파서, DocForge 위임 검증.
CSV/Excel 파싱은 DocForge 서비스로 위임되었으므로 DocForgeClient 테스트를 참조.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.pipeline.parsing.engine import (
    ALLOWED_EXTENSIONS,
    ParsingEngine,
    UnsupportedFormatError,
    _extract_extension,
)
from src.pipeline.parsing.base import (
    DocumentComplexity,
    DocumentProfile,
    ParseMetrics,
    ParseResult,
)
from src.pipeline.parsing.docforge_client import DocForgeResult
from src.pipeline.parsing.metrics import compute_markdown_metrics
from src.pipeline.parsing.pdf_analyzer import (
    _detect_table_hints,
    _determine_complexity,
)


# --- 확장자 추출 ---


class TestExtractExtension:

    def test_pdf(self):
        assert _extract_extension("report.pdf") == ".pdf"

    def test_csv(self):
        assert _extract_extension("data.csv") == ".csv"

    def test_xlsx(self):
        assert _extract_extension("sheet.xlsx") == ".xlsx"

    def test_xls(self):
        assert _extract_extension("legacy.xls") == ".xls"

    def test_case_insensitive(self):
        assert _extract_extension("REPORT.PDF") == ".pdf"
        assert _extract_extension("Data.CSV") == ".csv"

    def test_multiple_dots(self):
        assert _extract_extension("my.report.final.pdf") == ".pdf"

    def test_no_extension(self):
        assert _extract_extension("noext") == ""

    def test_hidden_file(self):
        assert _extract_extension(".gitignore") == ""


# --- UnsupportedFormatError ---


class TestUnsupportedFormatError:

    def test_error_message(self):
        err = UnsupportedFormatError("docx", ["pdf", "csv", "xlsx"])
        assert "docx" in str(err)
        assert "pdf" in str(err)

    def test_attributes(self):
        err = UnsupportedFormatError("pptx", ["pdf"])
        assert err.extension == "pptx"
        assert err.allowed == ["pdf"]


# --- ParsingEngine 라우팅 ---


class TestParsingEngineRouting:

    @pytest.fixture
    def engine(self):
        return ParsingEngine()

    def test_is_supported_pdf(self, engine):
        assert engine.is_supported("report.pdf") is True

    def test_is_supported_csv(self, engine):
        assert engine.is_supported("data.csv") is True

    def test_is_supported_xlsx(self, engine):
        assert engine.is_supported("sheet.xlsx") is True

    def test_is_supported_xls(self, engine):
        assert engine.is_supported("old.xls") is True

    def test_not_supported_docx(self, engine):
        assert engine.is_supported("report.docx") is False

    def test_not_supported_pptx(self, engine):
        assert engine.is_supported("slides.pptx") is False

    def test_not_supported_txt(self, engine):
        assert engine.is_supported("notes.txt") is False

    def test_allowed_extensions(self):
        exts = ParsingEngine.allowed_extensions()
        assert "pdf" in exts
        assert "csv" in exts
        assert "xlsx" in exts
        assert "xls" in exts

    @pytest.mark.asyncio
    async def test_deny_unsupported_extension(self, engine):
        """미지원 확장자 -> UnsupportedFormatError."""
        with pytest.raises(UnsupportedFormatError) as exc_info:
            await engine.parse(b"data", "report.docx")
        assert exc_info.value.extension == "docx"
        assert "pdf" in exc_info.value.allowed

    @pytest.mark.asyncio
    async def test_deny_no_extension(self, engine):
        with pytest.raises(UnsupportedFormatError):
            await engine.parse(b"data", "noext")

    @pytest.mark.asyncio
    async def test_deny_empty_filename(self, engine):
        with pytest.raises(ValueError, match="file_name"):
            await engine.parse(b"data", "")

    @pytest.mark.asyncio
    async def test_deny_empty_bytes(self, engine):
        with pytest.raises(ValueError, match="빈 파일"):
            await engine.parse(b"", "test.pdf")

    @pytest.mark.asyncio
    async def test_deny_image_formats(self, engine):
        """이미지 파일 직접 업로드 차단."""
        for ext in ["png", "jpg", "jpeg", "gif", "bmp"]:
            with pytest.raises(UnsupportedFormatError):
                await engine.parse(b"data", f"image.{ext}")

    @pytest.mark.asyncio
    async def test_deny_zip(self, engine):
        with pytest.raises(UnsupportedFormatError):
            await engine.parse(b"data", "archive.zip")

    @pytest.mark.asyncio
    async def test_csv_delegates_to_docforge(self, engine):
        """CSV 파일은 DocForge에 위임된다."""
        mock_result = DocForgeResult(
            markdown="| name | age |\n| --- | --- |\n| Alice | 30 |",
            metadata={"confidence": 0.95},
            stats={"parse_time_ms": 50},
        )
        with patch.object(engine._docforge_client, "parse", new_callable=AsyncMock, return_value=mock_result):
            result = await engine.parse(b"name,age\nAlice,30", "data.csv")

        assert isinstance(result, ParseResult)
        assert "Alice" in result.markdown
        assert result.source_mime_type == "text/csv"

    @pytest.mark.asyncio
    async def test_xlsx_delegates_to_docforge(self, engine):
        """xlsx 파일은 DocForge에 위임된다."""
        mock_result = DocForgeResult(
            markdown="| col1 | col2 |\n| --- | --- |\n| a | b |",
            metadata={},
            stats={},
        )
        with patch.object(engine._docforge_client, "parse", new_callable=AsyncMock, return_value=mock_result):
            result = await engine.parse(b"fake-xlsx", "sheet.xlsx")

        assert isinstance(result, ParseResult)
        assert result.source_mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    @pytest.mark.asyncio
    async def test_xls_delegates_to_docforge(self, engine):
        """xls 파일은 DocForge에 위임된다."""
        mock_result = DocForgeResult(
            markdown="| data |",
            metadata={},
            stats={},
        )
        with patch.object(engine._docforge_client, "parse", new_callable=AsyncMock, return_value=mock_result):
            result = await engine.parse(b"fake-xls", "legacy.xls")

        assert isinstance(result, ParseResult)
        assert result.source_mime_type == "application/vnd.ms-excel"


# --- PDF 분석기 ---


class TestPdfAnalyzer:

    def test_detect_table_hints_pipe(self):
        text = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        assert _detect_table_hints(text) is True

    def test_detect_table_hints_tab(self):
        text = "항목A\t항목B\t항목C\n값1\t값2\t값3\n값4\t값5\t값6"
        assert _detect_table_hints(text) is True

    def test_detect_table_hints_no_table(self):
        text = "이것은 일반 텍스트입니다.\n표가 없는 문단입니다."
        assert _detect_table_hints(text) is False

    def test_determine_complexity_text_only(self):
        complexity, parser = _determine_complexity(
            total_pages=10, text_pages=10, image_only_pages=0,
            has_tables=False, image_area_ratio=0.0, avg_chars=500,
        )
        assert complexity == DocumentComplexity.TEXT_ONLY
        assert parser == "pymupdf"

    def test_determine_complexity_with_tables(self):
        complexity, parser = _determine_complexity(
            total_pages=10, text_pages=10, image_only_pages=0,
            has_tables=True, image_area_ratio=0.05, avg_chars=500,
        )
        assert complexity == DocumentComplexity.TEXT_WITH_TABLES
        assert parser == "docforge"

    def test_determine_complexity_image_heavy(self):
        complexity, parser = _determine_complexity(
            total_pages=10, text_pages=2, image_only_pages=8,
            has_tables=False, image_area_ratio=0.8, avg_chars=20,
        )
        assert complexity == DocumentComplexity.IMAGE_HEAVY
        assert parser == "docforge"

    def test_determine_complexity_mixed(self):
        complexity, parser = _determine_complexity(
            total_pages=10, text_pages=7, image_only_pages=3,
            has_tables=False, image_area_ratio=0.3, avg_chars=300,
        )
        assert complexity == DocumentComplexity.MIXED
        assert parser == "docforge"


# --- 메트릭 ---


class TestMarkdownMetrics:

    def test_heading_count(self):
        md = "# Title\n\n## Section\n\ntext\n\n### Sub"
        metrics = compute_markdown_metrics(md)
        assert metrics.heading_count == 3

    def test_table_count(self):
        md = "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n| X | Y |\n| --- | --- |"
        metrics = compute_markdown_metrics(md)
        assert metrics.table_count == 2

    def test_list_count(self):
        md = "- item 1\n- item 2\n1. ordered\n2. second"
        metrics = compute_markdown_metrics(md)
        assert metrics.list_count == 4

    def test_empty_markdown(self):
        metrics = compute_markdown_metrics("")
        assert metrics.total_chars == 0
        assert metrics.heading_count == 0

    def test_parser_metadata(self):
        metrics = compute_markdown_metrics(
            "text", parser_used="pymupdf", parser_reason="test",
        )
        assert metrics.parser_used == "pymupdf"
        assert metrics.parser_reason == "test"


# --- PDF 파서 통합 테스트 ---


class TestPdfParserIntegration:
    """PyMuPDF로 합성 PDF를 생성하고 전체 파싱 경로를 검증한다."""

    @pytest.fixture
    def engine(self):
        return ParsingEngine()

    @staticmethod
    def _create_text_pdf(text: str = "테스트 문서입니다.\n\n이것은 본문입니다.") -> bytes:
        """PyMuPDF로 순수 텍스트 PDF를 합성한다."""
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text, fontname="helv", fontsize=12)
        pdf_bytes = doc.tobytes()
        doc.close()
        return pdf_bytes

    @pytest.mark.asyncio
    async def test_text_only_pdf_uses_pymupdf(self, engine):
        """텍스트만 있는 PDF -> pymupdf 경로 선택."""
        pdf_bytes = self._create_text_pdf()
        result = await engine.parse(pdf_bytes, "report.pdf")

        assert isinstance(result, ParseResult)
        assert result.metrics.parser_used == "pymupdf"
        assert result.metrics.total_chars > 0
        assert result.metrics.document_profile is not None
        assert result.metrics.document_profile.complexity == DocumentComplexity.TEXT_ONLY

    @pytest.mark.asyncio
    async def test_pdf_result_contains_text(self, engine):
        pdf_bytes = self._create_text_pdf("보험 약관 제1조")
        result = await engine.parse(pdf_bytes, "terms.pdf")
        # PyMuPDF가 실제로 텍스트를 추출했는지 확인
        assert len(result.markdown) > 0

    @pytest.mark.asyncio
    async def test_pdf_analyzer_profile(self):
        """analyze_pdf가 올바른 DocumentProfile을 반환한다."""
        from src.pipeline.parsing.pdf_analyzer import analyze_pdf

        # _MIN_CHARS_PER_PAGE(50) 이상의 텍스트를 넣어야 text_pages로 집계됨
        long_text = "이것은 테스트용 문서의 본문입니다. " * 10
        pdf_bytes = self._create_text_pdf(long_text)
        profile = analyze_pdf(pdf_bytes)

        assert profile.total_pages == 1
        assert profile.text_pages >= 1
        assert profile.image_only_pages == 0
        assert profile.total_chars > 0
        assert profile.complexity == DocumentComplexity.TEXT_ONLY
        assert profile.recommended_parser == "pymupdf"

    @pytest.mark.asyncio
    async def test_docforge_unavailable_pymupdf_fallback(self):
        """DocForge 미설정 + 표 있는 PDF -> pymupdf 폴백."""
        engine = ParsingEngine()
        pdf_bytes = self._create_text_pdf("| A | B |\n| --- | --- |\n| 1 | 2 |")
        result = await engine.parse(pdf_bytes, "table.pdf")
        # DocForge에 연결할 수 없으므로 pymupdf 폴백
        assert result.metrics.parser_used == "pymupdf"

    @pytest.mark.asyncio
    async def test_text_only_pdf_bypasses_docforge(self):
        """텍스트만 있는 PDF는 DocForge 없이 pymupdf 직접 추출."""
        engine = ParsingEngine()
        pdf_bytes = self._create_text_pdf()
        result = await engine.parse(pdf_bytes, "scan.pdf")
        assert result.metrics.parser_used == "pymupdf"


# --- Provider 어댑터 ---


class TestParsingEngineProvider:

    @pytest.mark.asyncio
    async def test_pdf_through_provider(self):
        """PDF를 Provider 어댑터를 통해 파싱한다."""
        from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

        engine = ParsingEngine()
        provider = ParsingEngineProvider(engine)

        pdf_bytes = TestPdfParserIntegration._create_text_pdf("Provider test text")
        result = await provider.parse(pdf_bytes, "application/pdf")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_csv_through_provider_delegates_to_docforge(self):
        """CSV를 Provider 어댑터를 통해 요청하면 DocForge에 위임된다."""
        from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

        engine = ParsingEngine()
        provider = ParsingEngineProvider(engine)

        mock_result = DocForgeResult(
            markdown="| name | age |\n| --- | --- |\n| Alice | 30 |",
            metadata={},
            stats={},
        )
        with patch.object(engine._docforge_client, "parse", new_callable=AsyncMock, return_value=mock_result):
            result = await provider.parse(b"name,age\nAlice,30", "text/csv")
        assert "Alice" in result
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_unsupported_mime_raises(self):
        from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

        engine = ParsingEngine()
        provider = ParsingEngineProvider(engine)

        with pytest.raises(ValueError, match="지원하지 않는"):
            await provider.parse(b"data", "application/zip")

    def test_supported_types(self):
        from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

        engine = ParsingEngine()
        provider = ParsingEngineProvider(engine)

        types = provider.supported_types()
        assert "application/pdf" in types
        assert "text/csv" in types


# --- Factory 연동 ---


class TestFactoryEngineIntegration:

    def test_factory_returns_engine_provider(self):
        from src.infrastructure.providers.factory import ProviderFactory
        from src.config import Settings
        from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

        s = Settings(parser_provider="engine")
        factory = ProviderFactory(s)
        provider = factory.get_parsing_provider()
        assert isinstance(provider, ParsingEngineProvider)
