"""ParsingProvider 테스트.

Provider 패턴 검증: TextParsingProvider 단위 테스트 + LlamaParse Mock 테스트.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.infrastructure.providers.base import ParsingProvider
from src.infrastructure.providers.parsing.text import TextParsingProvider
from src.infrastructure.providers.parsing.llama_parse import LlamaParseProvider


# --- TextParsingProvider ---


class TestTextParsingProvider:

    @pytest.fixture
    def parser(self):
        return TextParsingProvider()

    def test_implements_interface(self, parser):
        assert isinstance(parser, ParsingProvider)

    @pytest.mark.asyncio
    async def test_parse_plain_text(self, parser):
        content = "Hello World"
        result = await parser.parse(content.encode("utf-8"), "text/plain")
        assert result == "Hello World"

    @pytest.mark.asyncio
    async def test_parse_markdown(self, parser):
        md = "# Title\n\nParagraph"
        result = await parser.parse(md.encode("utf-8"), "text/markdown")
        assert "# Title" in result
        assert "Paragraph" in result

    @pytest.mark.asyncio
    async def test_parse_csv(self, parser):
        csv = "name,age\nAlice,30\nBob,25"
        result = await parser.parse(csv.encode("utf-8"), "text/csv")
        assert "Alice" in result
        assert "Bob" in result

    @pytest.mark.asyncio
    async def test_unsupported_type_raises(self, parser):
        with pytest.raises(ValueError, match="Unsupported"):
            await parser.parse(b"data", "application/zip")

    def test_supported_types(self, parser):
        types = parser.supported_types()
        assert "application/pdf" in types
        assert "text/markdown" in types
        assert "text/csv" in types
        assert "text/plain" in types


# --- LlamaParseProvider ---


class TestLlamaParseProvider:

    @pytest.fixture
    def parser(self):
        return LlamaParseProvider(api_key="test-key", timeout=10.0)

    def test_implements_interface(self, parser):
        assert isinstance(parser, ParsingProvider)

    def test_supported_types(self, parser):
        types = parser.supported_types()
        assert "application/pdf" in types
        assert "image/png" in types

    @pytest.mark.asyncio
    async def test_unsupported_type_raises(self, parser):
        with pytest.raises(ValueError, match="unsupported"):
            await parser.parse(b"data", "text/plain")

    @pytest.mark.asyncio
    async def test_parse_calls_api(self, parser):
        """LlamaParse REST API 호출 흐름을 Mock으로 검증한다."""
        upload_resp = MagicMock()
        upload_resp.json.return_value = {"id": "job-123"}
        upload_resp.raise_for_status = MagicMock()

        status_resp = MagicMock()
        status_resp.json.return_value = {"status": "SUCCESS"}
        status_resp.raise_for_status = MagicMock()

        result_resp = MagicMock()
        result_resp.json.return_value = {"markdown": "# Parsed\n\n| Col1 | Col2 |\n|---|---|\n| A | B |"}
        result_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=upload_resp)
        mock_client.get = AsyncMock(side_effect=[status_resp, result_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.infrastructure.providers.parsing.llama_parse.httpx.AsyncClient", return_value=mock_client):
            with patch("src.infrastructure.providers.parsing.llama_parse.asyncio.sleep", new_callable=AsyncMock):
                markdown = await parser.parse(b"%PDF-fake", "application/pdf")

        assert "# Parsed" in markdown
        assert "Col1" in markdown
        mock_client.post.assert_called_once()
        assert mock_client.get.call_count == 2  # status + result

    @pytest.mark.asyncio
    async def test_parse_error_raises(self, parser):
        """LlamaParse가 ERROR를 반환하면 RuntimeError."""
        upload_resp = MagicMock()
        upload_resp.json.return_value = {"id": "job-err"}
        upload_resp.raise_for_status = MagicMock()

        error_resp = MagicMock()
        error_resp.json.return_value = {"status": "ERROR", "error": "Bad PDF"}
        error_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=upload_resp)
        mock_client.get = AsyncMock(return_value=error_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.infrastructure.providers.parsing.llama_parse.httpx.AsyncClient", return_value=mock_client):
            with patch("src.infrastructure.providers.parsing.llama_parse.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(RuntimeError, match="Bad PDF"):
                    await parser.parse(b"%PDF-fake", "application/pdf")


# --- ProviderFactory ---


class TestParsingProviderFactory:

    def test_factory_returns_text_by_default(self):
        from src.infrastructure.providers.factory import ProviderFactory
        from src.config import Settings

        s = Settings(parser_provider="text")
        factory = ProviderFactory(s)
        provider = factory.get_parsing_provider()
        assert isinstance(provider, TextParsingProvider)

    def test_factory_llamaparse_requires_key(self):
        from src.infrastructure.providers.factory import ProviderFactory
        from src.config import Settings

        s = Settings(parser_provider="llamaparse", llamaparse_api_key="")
        factory = ProviderFactory(s)
        with pytest.raises(ValueError, match="API_KEY"):
            factory.get_parsing_provider()

    def test_factory_llamaparse_with_key(self):
        from src.infrastructure.providers.factory import ProviderFactory
        from src.config import Settings

        s = Settings(parser_provider="llamaparse", llamaparse_api_key="llx-test-key")
        factory = ProviderFactory(s)
        provider = factory.get_parsing_provider()
        assert isinstance(provider, LlamaParseProvider)
