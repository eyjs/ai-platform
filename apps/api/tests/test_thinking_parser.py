"""Thinking 파서 단위 테스트.

HttpLLMProvider의 <think>...</think> 블록 분리 검증:
- split_thinking: 완성된 텍스트에서 thinking/answer 분리
- generate_stream_typed: SSE 스트리밍에서 thinking/answer StreamChunk 분리
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.infrastructure.providers.llm.http_llm import HttpLLMProvider
from src.infrastructure.providers.base import StreamChunk


# --- split_thinking 단위 테스트 ---


class TestSplitThinking:

    def test_no_thinking_block(self):
        thinking, answer = HttpLLMProvider.split_thinking("보험료는 100만원입니다.")
        assert thinking == ""
        assert answer == "보험료는 100만원입니다."

    def test_with_thinking_block(self):
        text = "<think>사용자가 보험료를 물어보고 있다.</think>보험료는 100만원입니다."
        thinking, answer = HttpLLMProvider.split_thinking(text)
        assert thinking == "사용자가 보험료를 물어보고 있다."
        assert answer == "보험료는 100만원입니다."

    def test_multiline_thinking(self):
        text = "<think>\n1단계: 질문 분석\n2단계: 문서 검색\n</think>\n답변입니다."
        thinking, answer = HttpLLMProvider.split_thinking(text)
        assert "1단계" in thinking
        assert "2단계" in thinking
        assert answer == "답변입니다."

    def test_thinking_only_no_answer(self):
        text = "<think>생각만 했습니다.</think>"
        thinking, answer = HttpLLMProvider.split_thinking(text)
        assert thinking == "생각만 했습니다."
        assert answer == ""

    def test_empty_thinking_block(self):
        text = "<think></think>답변입니다."
        thinking, answer = HttpLLMProvider.split_thinking(text)
        assert thinking == ""
        assert answer == "답변입니다."

    def test_whitespace_around_answer(self):
        text = "<think>생각</think>  \n  답변  "
        thinking, answer = HttpLLMProvider.split_thinking(text)
        assert thinking == "생각"
        assert answer == "답변"

    def test_plain_empty_text(self):
        thinking, answer = HttpLLMProvider.split_thinking("")
        assert thinking == ""
        assert answer == ""

    def test_whitespace_only(self):
        thinking, answer = HttpLLMProvider.split_thinking("   \n  ")
        assert thinking == ""
        assert answer == ""

    def test_thinking_with_special_chars(self):
        text = "<think>점수: 0.95, 표: | A | B |</think>결과입니다."
        thinking, answer = HttpLLMProvider.split_thinking(text)
        assert "0.95" in thinking
        assert "| A | B |" in thinking
        assert answer == "결과입니다."


# --- 스트리밍 파서 테스트 ---


def _make_sse_line(content: str) -> str:
    """SSE data 라인을 생성한다."""
    data = {"choices": [{"delta": {"content": content}}]}
    return f"data: {json.dumps(data, ensure_ascii=False)}"


def _make_sse_done() -> str:
    return "data: [DONE]"


class TestStreamTypedThinkingParser:
    """generate_stream_typed의 <think> 태그 상태 전환 검증."""

    @pytest.fixture
    def provider(self):
        return HttpLLMProvider(base_url="http://localhost:8080")

    @pytest.mark.asyncio
    async def test_no_thinking_all_answer(self, provider):
        """thinking 없으면 모두 answer."""
        lines = [
            _make_sse_line("보험료는 "),
            _make_sse_line("100만원입니다."),
            _make_sse_done(),
        ]
        chunks = await _collect_stream(provider, lines)
        assert all(c.kind == "answer" for c in chunks)
        assert "".join(c.content for c in chunks) == "보험료는 100만원입니다."

    @pytest.mark.asyncio
    async def test_think_open_close_in_separate_tokens(self, provider):
        """<think>와 </think>가 별도 토큰으로 올 때."""
        lines = [
            _make_sse_line("<think>"),
            _make_sse_line("분석 중..."),
            _make_sse_line("</think>"),
            _make_sse_line("답변입니다."),
            _make_sse_done(),
        ]
        chunks = await _collect_stream(provider, lines)
        thinking = "".join(c.content for c in chunks if c.kind == "thinking")
        answer = "".join(c.content for c in chunks if c.kind == "answer")
        assert "분석 중..." in thinking
        assert "답변입니다." in answer

    @pytest.mark.asyncio
    async def test_think_block_in_single_token(self, provider):
        """<think>...</think>가 하나의 토큰에 있을 때."""
        lines = [
            _make_sse_line("<think>빠른 생각</think>답변"),
            _make_sse_done(),
        ]
        chunks = await _collect_stream(provider, lines)
        thinking = "".join(c.content for c in chunks if c.kind == "thinking")
        answer = "".join(c.content for c in chunks if c.kind == "answer")
        assert "빠른 생각" in thinking
        assert "답변" in answer

    @pytest.mark.asyncio
    async def test_text_before_think_tag(self, provider):
        """<think> 앞에 텍스트가 있으면 answer로 전달."""
        lines = [
            _make_sse_line("서론<think>생각</think>결론"),
            _make_sse_done(),
        ]
        chunks = await _collect_stream(provider, lines)
        answer = "".join(c.content for c in chunks if c.kind == "answer")
        thinking = "".join(c.content for c in chunks if c.kind == "thinking")
        assert "서론" in answer
        assert "결론" in answer
        assert "생각" in thinking

    @pytest.mark.asyncio
    async def test_think_close_with_remainder(self, provider):
        """</think> 뒤에 바로 텍스트가 이어질 때."""
        lines = [
            _make_sse_line("<think>"),
            _make_sse_line("생각 내용"),
            _make_sse_line("</think>바로 답변 시작"),
            _make_sse_done(),
        ]
        chunks = await _collect_stream(provider, lines)
        thinking = "".join(c.content for c in chunks if c.kind == "thinking")
        answer = "".join(c.content for c in chunks if c.kind == "answer")
        assert "생각 내용" in thinking
        assert "바로 답변 시작" in answer

    @pytest.mark.asyncio
    async def test_empty_delta_skipped(self, provider):
        """content가 빈 delta는 무시."""
        lines = [
            "data: " + json.dumps({"choices": [{"delta": {}}]}),
            _make_sse_line("답변"),
            _make_sse_done(),
        ]
        chunks = await _collect_stream(provider, lines)
        assert len(chunks) == 1
        assert chunks[0].content == "답변"

    @pytest.mark.asyncio
    async def test_non_data_lines_skipped(self, provider):
        """SSE data: 접두사가 없는 라인은 무시."""
        lines = [
            "event: ping",
            ": comment",
            _make_sse_line("답변"),
            _make_sse_done(),
        ]
        chunks = await _collect_stream(provider, lines)
        assert len(chunks) == 1


# --- 헬퍼 ---


async def _collect_stream(
    provider: HttpLLMProvider,
    sse_lines: list[str],
) -> list[StreamChunk]:
    """Mock HTTP 스트림에서 StreamChunk를 수집한다."""

    async def fake_aiter_lines():
        for line in sse_lines:
            yield line

    mock_response = AsyncMock()
    mock_response.aiter_lines = fake_aiter_lines
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    with patch.object(provider._client, "stream", return_value=mock_response):
        chunks = []
        async for chunk in provider.generate_stream_typed("test"):
            chunks.append(chunk)
        return chunks
