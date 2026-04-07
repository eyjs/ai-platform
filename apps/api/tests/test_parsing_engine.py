"""통합 파싱 엔진 테스트.

ParsingEngine 라우팅, 확장자 감지, deny, 각 파서 단위 검증.
"""

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
from src.pipeline.parsing.csv_parser import CsvParser, _rows_to_markdown_table, _detect_dialect
from src.pipeline.parsing.excel_parser import ExcelParser
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
        return ParsingEngine(enable_docling=False, enable_vlm=False)

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
        """미지원 확장자 → UnsupportedFormatError."""
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


# --- CSV 파서 ---


class TestCsvParser:

    @pytest.fixture
    def parser(self):
        return CsvParser()

    @pytest.mark.asyncio
    async def test_basic_csv(self, parser):
        csv_data = "이름,나이,부서\n홍길동,30,개발팀\n김철수,25,기획팀"
        result = await parser.parse(csv_data.encode("utf-8"), "test.csv")

        assert isinstance(result, ParseResult)
        assert "홍길동" in result.markdown
        assert "| 이름 | 나이 | 부서 |" in result.markdown
        assert "| --- | --- | --- |" in result.markdown
        assert result.metrics.parser_used == "csv"
        assert result.metrics.table_count >= 1

    @pytest.mark.asyncio
    async def test_semicolon_delimiter(self, parser):
        csv_data = "name;age\nAlice;30\nBob;25"
        result = await parser.parse(csv_data.encode("utf-8"), "test.csv")
        assert "Alice" in result.markdown
        assert "Bob" in result.markdown

    @pytest.mark.asyncio
    async def test_empty_csv(self, parser):
        result = await parser.parse(b"", "empty.csv")
        assert result.markdown == ""

    @pytest.mark.asyncio
    async def test_max_rows_truncation(self):
        parser = CsvParser(max_rows=5)
        # 헤더 1행 + 데이터 100행 = 101행 입력, max_rows=5이면 데이터 5행까지만
        rows = ["col1,col2"] + [f"a{i},b{i}" for i in range(100)]
        csv_data = "\n".join(rows)
        result = await parser.parse(csv_data.encode("utf-8"), "big.csv")
        assert "잘렸습니다" in result.markdown

    @pytest.mark.asyncio
    async def test_exact_max_rows_no_truncation(self):
        """헤더 + 정확히 max_rows행 → 잘림 없음."""
        parser = CsvParser(max_rows=3)
        rows = ["col1,col2", "a1,b1", "a2,b2", "a3,b3"]  # 헤더 + 3행 = max_rows
        csv_data = "\n".join(rows)
        result = await parser.parse(csv_data.encode("utf-8"), "exact.csv")
        assert "잘렸습니다" not in result.markdown

    @pytest.mark.asyncio
    async def test_pipe_escape(self, parser):
        csv_data = "name,value\ntest|name,100"
        result = await parser.parse(csv_data.encode("utf-8"), "pipe.csv")
        assert "\\|" in result.markdown

    def test_rows_to_markdown_table(self):
        rows = [["A", "B"], ["1", "2"], ["3", "4"]]
        md = _rows_to_markdown_table(rows, "test.csv")
        assert "| A | B |" in md
        assert "| 1 | 2 |" in md

    def test_detect_dialect_comma(self):
        d = _detect_dialect("a,b,c\n1,2,3")
        assert d.delimiter == ","

    def test_detect_dialect_semicolon(self):
        d = _detect_dialect("a;b;c\n1;2;3\n4;5;6")
        assert d.delimiter == ";"


# --- Excel 파서 ---


class TestExcelParser:

    @pytest.fixture
    def parser(self):
        return ExcelParser()

    @pytest.mark.asyncio
    async def test_basic_xlsx(self, parser):
        """openpyxl로 간단한 xlsx를 생성하고 파싱한다."""
        xlsx_bytes = _create_test_xlsx([
            ("Sheet1", [["이름", "나이"], ["홍길동", "30"], ["김철수", "25"]]),
        ])
        result = await parser.parse(xlsx_bytes, "test.xlsx")

        assert isinstance(result, ParseResult)
        assert "홍길동" in result.markdown
        assert "Sheet1" in result.markdown
        assert "| 이름 | 나이 |" in result.markdown
        assert result.metrics.parser_used == "excel"

    @pytest.mark.asyncio
    async def test_multi_sheet(self, parser):
        xlsx_bytes = _create_test_xlsx([
            ("매출", [["월", "금액"], ["1월", "1000"]]),
            ("비용", [["항목", "금액"], ["인건비", "500"]]),
        ])
        result = await parser.parse(xlsx_bytes, "multi.xlsx")
        assert "매출" in result.markdown
        assert "비용" in result.markdown

    @pytest.mark.asyncio
    async def test_empty_sheet_skipped(self, parser):
        xlsx_bytes = _create_test_xlsx([
            ("빈시트", []),
            ("데이터", [["A", "B"], ["1", "2"]]),
        ])
        result = await parser.parse(xlsx_bytes, "empty_sheet.xlsx")
        assert "빈시트" not in result.markdown
        assert "데이터" in result.markdown

    @pytest.mark.asyncio
    async def test_empty_header_gets_column_name(self, parser):
        xlsx_bytes = _create_test_xlsx([
            ("Sheet1", [["", "값"], ["데이터", "100"]]),
        ])
        result = await parser.parse(xlsx_bytes, "no_header.xlsx")
        assert "Column_1" in result.markdown


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
        assert parser == "docling"

    def test_determine_complexity_image_heavy(self):
        complexity, parser = _determine_complexity(
            total_pages=10, text_pages=2, image_only_pages=8,
            has_tables=False, image_area_ratio=0.8, avg_chars=20,
        )
        assert complexity == DocumentComplexity.IMAGE_HEAVY
        assert parser == "vlm"

    def test_determine_complexity_mixed(self):
        complexity, parser = _determine_complexity(
            total_pages=10, text_pages=7, image_only_pages=3,
            has_tables=False, image_area_ratio=0.3, avg_chars=300,
        )
        assert complexity == DocumentComplexity.MIXED
        assert parser == "docling"


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
        return ParsingEngine(enable_docling=False, enable_vlm=False)

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
        """텍스트만 있는 PDF → pymupdf 경로 선택."""
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
    async def test_docling_disabled_fallback(self):
        """docling 비활성 + 표 있는 PDF → pymupdf 폴백."""
        engine = ParsingEngine(enable_docling=False, enable_vlm=False)
        pdf_bytes = self._create_text_pdf("| A | B |\n| --- | --- |\n| 1 | 2 |")
        result = await engine.parse(pdf_bytes, "table.pdf")
        # docling 비활성이므로 pymupdf 폴백
        assert result.metrics.parser_used == "pymupdf"

    @pytest.mark.asyncio
    async def test_vlm_disabled_fallback(self):
        """vlm 비활성 → docling 또는 pymupdf 폴백."""
        engine = ParsingEngine(enable_docling=False, enable_vlm=False)
        pdf_bytes = self._create_text_pdf()
        result = await engine.parse(pdf_bytes, "scan.pdf")
        # 둘 다 비활성이므로 pymupdf
        assert result.metrics.parser_used == "pymupdf"


# --- Provider 어댑터 ---


class TestParsingEngineProvider:

    @pytest.mark.asyncio
    async def test_csv_through_provider(self):
        from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

        engine = ParsingEngine(enable_docling=False, enable_vlm=False)
        provider = ParsingEngineProvider(engine)

        csv_data = "name,age\nAlice,30"
        result = await provider.parse(csv_data.encode("utf-8"), "text/csv")
        assert "Alice" in result
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_unsupported_mime_raises(self):
        from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

        engine = ParsingEngine(enable_docling=False, enable_vlm=False)
        provider = ParsingEngineProvider(engine)

        with pytest.raises(ValueError, match="지원하지 않는"):
            await provider.parse(b"data", "application/zip")

    def test_supported_types(self):
        from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

        engine = ParsingEngine(enable_docling=False, enable_vlm=False)
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


# --- 헬퍼 ---


def _create_test_xlsx(sheets: list[tuple[str, list[list[str]]]]) -> bytes:
    """테스트용 xlsx 바이트를 생성한다."""
    import io
    import openpyxl

    wb = openpyxl.Workbook()
    # 기본 시트 제거
    wb.remove(wb.active)

    for sheet_name, rows in sheets:
        ws = wb.create_sheet(title=sheet_name)
        for row in rows:
            ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
