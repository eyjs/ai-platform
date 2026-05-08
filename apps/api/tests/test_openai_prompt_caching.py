"""T7: OpenAI Prompt Caching 구조 검증."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.providers.llm.openai import OpenAILLMProvider
from src.infrastructure.providers.base import ProviderCapability


def _make_provider(system_prefix: str = "") -> OpenAILLMProvider:
    """mock client로 OpenAI provider 생성."""
    with patch("src.infrastructure.providers.llm.openai.OpenAILLMProvider.__init__", lambda self, **kw: None):
        provider = OpenAILLMProvider.__new__(OpenAILLMProvider)
        provider._client = AsyncMock()
        provider._model = "gpt-4o-mini"
        provider._system_prefix = system_prefix
        provider._max_tokens = 4096
        return provider


def test_capability_supports_prompt_caching():
    """capability에 supports_prompt_caching=True."""
    provider = _make_provider()
    assert provider.capability.supports_prompt_caching is True


def test_build_messages_no_prefix_no_system():
    """prefix도 system도 없으면 user만."""
    provider = _make_provider(system_prefix="")
    messages = provider._build_messages("hello")
    assert len(messages) == 1
    assert messages[0] == {"role": "user", "content": "hello"}


def test_build_messages_system_only():
    """system만 있으면 단순 string content."""
    provider = _make_provider(system_prefix="")
    messages = provider._build_messages("hello", system="You are helpful.")
    assert len(messages) == 2
    assert messages[0] == {"role": "system", "content": "You are helpful."}
    assert messages[1] == {"role": "user", "content": "hello"}


def test_build_messages_prefix_only():
    """prefix만 있으면 단순 string content."""
    provider = _make_provider(system_prefix="Base instructions.")
    messages = provider._build_messages("hello", system="")
    assert len(messages) == 2
    assert messages[0] == {"role": "system", "content": "Base instructions."}
    assert messages[1] == {"role": "user", "content": "hello"}


def test_build_messages_prefix_and_system_structured():
    """prefix + system이 모두 있으면 구조화된 content blocks로 분리.

    이 구조가 OpenAI automatic prompt caching의 prefix 매칭률을 높인다.
    """
    provider = _make_provider(system_prefix="You are a base assistant. Always be helpful.")
    messages = provider._build_messages("hello", system="Insurance domain specifics.")

    assert len(messages) == 2
    system_msg = messages[0]
    assert system_msg["role"] == "system"

    # content가 list of blocks
    assert isinstance(system_msg["content"], list)
    assert len(system_msg["content"]) == 2

    # 첫 번째 블록: prefix (캐시 가능)
    assert system_msg["content"][0] == {
        "type": "text",
        "text": "You are a base assistant. Always be helpful.",
    }
    # 두 번째 블록: dynamic system
    assert system_msg["content"][1] == {
        "type": "text",
        "text": "Insurance domain specifics.",
    }


async def test_generate_calls_with_structured_messages():
    """generate()가 구조화된 메시지를 client에 전달한다."""
    provider = _make_provider(system_prefix="Base instructions.")

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "response"
    mock_response.usage = None
    provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

    result = await provider.generate("hello", system="Dynamic.")
    assert result == "response"

    # 호출된 messages 확인
    call_args = provider._client.chat.completions.create.call_args
    messages = call_args[1]["messages"]
    assert messages[0]["role"] == "system"
    assert isinstance(messages[0]["content"], list)
    assert len(messages[0]["content"]) == 2


async def test_generate_logs_cache_usage():
    """응답에 cached_tokens가 있으면 로깅한다."""
    provider = _make_provider(system_prefix="")

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "response"
    # usage with cached tokens
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_details = MagicMock()
    mock_details.cached_tokens = 80
    mock_usage.prompt_tokens_details = mock_details
    mock_response.usage = mock_usage

    provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("src.infrastructure.providers.llm.openai.logger") as mock_logger:
        await provider.generate("hello", system="test")
        mock_logger.debug.assert_called_once()
        call_args = mock_logger.debug.call_args
        assert "openai_prompt_cache_hit" in str(call_args)


async def test_generate_no_log_when_no_cache():
    """cached_tokens가 0이면 로깅하지 않는다."""
    provider = _make_provider(system_prefix="")

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "response"
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_details = MagicMock()
    mock_details.cached_tokens = 0
    mock_usage.prompt_tokens_details = mock_details
    mock_response.usage = mock_usage

    provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("src.infrastructure.providers.llm.openai.logger") as mock_logger:
        await provider.generate("hello", system="test")
        mock_logger.debug.assert_not_called()
