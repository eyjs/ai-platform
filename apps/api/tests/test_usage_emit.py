"""T110: done 봉투 usage emit 검증.

테스트 범위:
  1. agentic _stream_agentic done 봉투에 usage 키 + 구조 포함
  2. usage 미가용(이벤트 없음) 시 usage 필드 0값 포함 (throw 없음, SSE 비차단)
  3. AnthropicLLMProvider._extract_usage dict 반환
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────────────────────

def _make_stream_executor():
    """테스트용 GraphExecutor (agentic 스트리밍)."""
    from src.agent.graph_executor import GraphExecutor
    from src.agent.graph_cache import GraphCache

    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="test answer")
    mock_registry = MagicMock()
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_registry.get.return_value = mock_tool

    return GraphExecutor(
        main_llm=mock_llm,
        tool_registry=mock_registry,
        guardrails={},
        chat_model=MagicMock(),
        graph_cache=GraphCache(),
    )


def _agentic_plan(system_prompt: str = "You are a test assistant."):
    """agentic 모드 ExecutionPlan."""
    from src.agent.graph_executor import AgentMode
    from src.domain.models import SearchScope
    from src.domain.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType, ToolCall

    return ExecutionPlan(
        mode=AgentMode.AGENTIC,
        scope=SearchScope(),
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True),
        tool_groups=[[ToolCall("test_tool", {})]],
        system_prompt=system_prompt,
    )


def _make_chat_model_end_event(input_tokens: int, output_tokens: int,
                               cache_read: int = 0, cache_creation: int = 0):
    """on_chat_model_end 이벤트 Mock 생성."""
    output_msg = MagicMock()
    output_msg.response_metadata = {
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        }
    }
    output_msg.usage_metadata = None
    return {
        "event": "on_chat_model_end",
        "data": {"output": output_msg},
    }


def _make_token_event(text: str):
    """on_chat_model_stream 토큰 이벤트 Mock."""
    chunk = MagicMock()
    chunk.content = text
    chunk.tool_calls = None
    chunk.usage_metadata = None
    return {
        "event": "on_chat_model_stream",
        "data": {"chunk": chunk},
    }


# ──────────────────────────────────────────────────────────────────────────────
# 테스트 1: agentic done 봉투에 usage 키 + 구조
# ──────────────────────────────────────────────────────────────────────────────

@patch("src.agent.executors.agentic_executor.build_agentic_graph")
@patch("src.agent.executors.agentic_executor.convert_tools_to_langchain")
async def test_agentic_done_has_usage_key(mock_convert, mock_build):
    """agentic _stream_agentic done 봉투에 usage 키가 있고 필수 필드를 포함한다."""
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    # astream_events가 토큰 + usage 이벤트를 반환하는 mock 그래프
    events = [
        _make_token_event("안녕하세요"),
        _make_chat_model_end_event(input_tokens=150, output_tokens=42, cache_read=80),
    ]

    async def _astream_events(*args, **kwargs):
        for e in events:
            yield e

    mock_app = MagicMock()
    mock_app.astream_events = _astream_events
    mock_build.return_value = mock_app

    executor = _make_stream_executor()
    plan = _agentic_plan()

    collected = []
    async for event in executor.execute_stream("테스트 질문", plan, "sess-110"):
        collected.append(event)

    done_events = [e for e in collected if e.get("type") == "done"]
    assert len(done_events) == 1, "done 이벤트 정확히 1개"

    done_data = done_events[0]["data"]
    assert "usage" in done_data, "done.data에 usage 키 존재"

    usage = done_data["usage"]
    # 필수 필드 존재
    assert "input_tokens" in usage
    assert "output_tokens" in usage
    assert "cache_read_input_tokens" in usage
    # 값 검증
    assert usage["input_tokens"] == 150
    assert usage["output_tokens"] == 42
    assert usage["cache_read_input_tokens"] == 80
    # workflow{} 필드 건드리지 않음 (workflow 키 없어야 함 — agentic 경로)
    assert "workflow" not in done_data, "agentic done에 workflow 키 없음"


@patch("src.agent.executors.agentic_executor.build_agentic_graph")
@patch("src.agent.executors.agentic_executor.convert_tools_to_langchain")
async def test_agentic_done_usage_structure_types(mock_convert, mock_build):
    """usage 필드 값은 int 타입이어야 한다."""
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    events = [
        _make_token_event("응답"),
        _make_chat_model_end_event(input_tokens=200, output_tokens=30),
    ]

    async def _astream_events(*args, **kwargs):
        for e in events:
            yield e

    mock_app = MagicMock()
    mock_app.astream_events = _astream_events
    mock_build.return_value = mock_app

    executor = _make_stream_executor()
    plan = _agentic_plan()

    done_data = None
    async for event in executor.execute_stream("질문", plan, "sess-111"):
        if event.get("type") == "done":
            done_data = event["data"]

    assert done_data is not None
    usage = done_data["usage"]
    assert isinstance(usage["input_tokens"], int)
    assert isinstance(usage["output_tokens"], int)
    assert isinstance(usage["cache_read_input_tokens"], int)


# ──────────────────────────────────────────────────────────────────────────────
# 테스트 2: usage 미가용 시 throw 없음, 0값 포함
# ──────────────────────────────────────────────────────────────────────────────

@patch("src.agent.executors.agentic_executor.build_agentic_graph")
@patch("src.agent.executors.agentic_executor.convert_tools_to_langchain")
async def test_agentic_done_usage_absent_graceful(mock_convert, mock_build):
    """usage 정보가 없는 이벤트만 올 때 throw 없이 usage 0값 포함."""
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    # 토큰 이벤트만, usage 이벤트 없음
    events = [_make_token_event("응답 텍스트")]

    async def _astream_events(*args, **kwargs):
        for e in events:
            yield e

    mock_app = MagicMock()
    mock_app.astream_events = _astream_events
    mock_build.return_value = mock_app

    executor = _make_stream_executor()
    plan = _agentic_plan()

    done_data = None
    # 예외 없이 실행되어야 함
    async for event in executor.execute_stream("질문", plan, "sess-112"):
        if event.get("type") == "done":
            done_data = event["data"]

    assert done_data is not None, "done 이벤트 수신"
    # usage 키는 있어야 함 (0값 포함)
    assert "usage" in done_data, "usage absent 시 0값으로 포함"
    usage = done_data["usage"]
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert usage["cache_read_input_tokens"] == 0


@patch("src.agent.executors.agentic_executor.build_agentic_graph")
@patch("src.agent.executors.agentic_executor.convert_tools_to_langchain")
async def test_agentic_done_empty_event_stream_graceful(mock_convert, mock_build):
    """이벤트 스트림이 완전히 비어있어도 done 봉투 정상 방출 + throw 없음."""
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    async def _astream_events(*args, **kwargs):
        return
        yield  # noqa: unreachable — make generator

    mock_app = MagicMock()
    mock_app.astream_events = _astream_events
    mock_build.return_value = mock_app

    executor = _make_stream_executor()
    plan = _agentic_plan()

    done_events = []
    async for event in executor.execute_stream("질문", plan, "sess-113"):
        if event.get("type") == "done":
            done_events.append(event)

    assert len(done_events) == 1
    assert "usage" in done_events[0]["data"]


# ──────────────────────────────────────────────────────────────────────────────
# 테스트 3: (삭제됨) anthropic _extract_usage dict 반환
# ──────────────────────────────────────────────────────────────────────────────
#
# AnthropicLLMProvider._extract_usage 를 겨냥한 5개 테스트는 2026-07-16 상용 퇴역과 함께
# 지웠다. 그 메서드는 Anthropic usage 객체(cache_read_input_tokens 등)를 파싱하는 벤더
# 전용 코드였고, 벤더가 사라지면서 같이 사라졌다.
#
# done 봉투에 usage 를 싣는 계약 자체는 위 테스트 1~2 가 벤더와 무관하게 지킨다.
