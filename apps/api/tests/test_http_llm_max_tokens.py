"""HttpLLMProvider max_tokens 전달 검증."""

import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_generate_includes_max_tokens():
    from src.infrastructure.providers.llm.http_llm import HttpLLMProvider
    provider = HttpLLMProvider("http://localhost:8080", max_tokens=4096)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "답변"}}],
    }
    with patch.object(provider._client, "post", return_value=mock_response) as mock_post:
        await provider.generate("질문")
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_default_max_tokens():
    from src.infrastructure.providers.llm.http_llm import HttpLLMProvider
    provider = HttpLLMProvider("http://localhost:8080")
    assert provider._max_tokens == 4096
