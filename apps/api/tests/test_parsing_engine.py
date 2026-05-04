"""통합 파싱 엔진 테스트.

ParsingEngine 라우팅, 확장자 감지, deny, DocForge 위임 검증.
모든 파싱은 DocForge API에 위임된다.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.pipeline.parsing.engine import (
    ALLOWED_EXTENSIONS,
    ParsingEngine,
    UnsupportedFormatError,
    _extract_extension,
)
from src.pipeline.parsing.base import ParseMetrics, ParseResult
from src.pipeline.parsing.docforge_client import DocForgeResult
from src.pipeline.parsing.metrics import compute_markdown_metrics


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
        for ext in ["png", "jpg", "jpeg", "gif", "bmp"]:
            with pytest.raises(UnsupportedFormatError):
                await engine.parse(b"data", f"image.{ext}")

    @pytest.mark.asyncio
    async def test_deny_zip(self, engine):
        with pytest.raises(UnsupportedFormatError):
            await engine.parse(b"data", "archive.zip")


# --- DocForge 위임 ---


class TestDocForgeDelegation:

    @pytest.fixture
    def engine(self):
        return ParsingEngine()

    @pytest.mark.asyncio
    async def test_pdf_delegates_to_docforge(self, engine):
        mock_result = DocForgeResult(
            markdown="# 계약서\n\n제1조 ...",
            metadata={"total_pages": 3, "confidence": 0.95},
            stats={"parse_time_ms": 200},
        )
        with patch.object(engine._docforge_client, "parse", new_callable=AsyncMock, return_value=mock_result):
            result = await engine.parse(b"fake-pdf", "contract.pdf")

        assert isinstance(result, ParseResult)
        assert "계약서" in result.markdown
        assert result.source_mime_type == "application/pdf"
        assert result.metrics.parser_used == "docforge_pdf"
        assert result.metrics.total_pages == 3

    @pytest.mark.asyncio
    async def test_csv_delegates_to_docforge(self, engine):
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
        assert result.metrics.parser_used == "docforge_csv"

    @pytest.mark.asyncio
    async def test_xlsx_delegates_to_docforge(self, engine):
        mock_result = DocForgeResult(
            markdown="| col1 | col2 |\n| --- | --- |\n| a | b |",
            metadata={},
            stats={},
        )
        with patch.object(engine._docforge_client, "parse", new_callable=AsyncMock, return_value=mock_result):
            result = await engine.parse(b"fake-xlsx", "sheet.xlsx")

        assert isinstance(result, ParseResult)
        assert result.source_mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert result.metrics.parser_used == "docforge_excel"

    @pytest.mark.asyncio
    async def test_xls_delegates_to_docforge(self, engine):
        mock_result = DocForgeResult(
            markdown="| data |",
            metadata={},
            stats={},
        )
        with patch.object(engine._docforge_client, "parse", new_callable=AsyncMock, return_value=mock_result):
            result = await engine.parse(b"fake-xls", "legacy.xls")

        assert isinstance(result, ParseResult)
        assert result.source_mime_type == "application/vnd.ms-excel"
        assert result.metrics.parser_used == "docforge_excel"

    @pytest.mark.asyncio
    async def test_docforge_passes_correct_mime(self, engine):
        """DocForge에 올바른 MIME 타입이 전달되는지 확인."""
        mock_result = DocForgeResult(markdown="ok", metadata={}, stats={})
        mock_parse = AsyncMock(return_value=mock_result)

        with patch.object(engine._docforge_client, "parse", mock_parse):
            await engine.parse(b"data", "report.pdf")

        mock_parse.assert_called_once_with(
            file_bytes=b"data",
            file_name="report.pdf",
            mime_type="application/pdf",
        )


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
            "text", parser_used="docforge_pdf", parser_reason="test",
        )
        assert metrics.parser_used == "docforge_pdf"
        assert metrics.parser_reason == "test"


# --- Provider 어댑터 ---


class TestParsingEngineProvider:

    @pytest.mark.asyncio
    async def test_pdf_through_provider(self):
        from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

        engine = ParsingEngine()
        provider = ParsingEngineProvider(engine)

        mock_result = DocForgeResult(
            markdown="# Test PDF content",
            metadata={"total_pages": 1},
            stats={},
        )
        with patch.object(engine._docforge_client, "parse", new_callable=AsyncMock, return_value=mock_result):
            result = await provider.parse(b"fake-pdf", "application/pdf")
        assert isinstance(result, str)
        assert "Test PDF" in result

    @pytest.mark.asyncio
    async def test_csv_through_provider_delegates_to_docforge(self):
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
