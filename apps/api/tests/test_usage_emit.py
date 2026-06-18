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

from src.infrastructure.providers.llm.anthropic import AnthropicStubProvider


# ──────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────────────────────

def _make_stream_executor():
    """테스트용 GraphExecutor (agentic 스트리밍)."""
    from src.agent.graph_executor import GraphExecutor
    from src.router.graph_cache import GraphCache

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
    from src.router.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType, ToolCall

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

@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
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


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
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

@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
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


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
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
# 테스트 3: anthropic _extract_usage dict 반환
# ──────────────────────────────────────────────────────────────────────────────

def test_extract_usage_returns_dict_with_all_fields():
    """_extract_usage가 usage 객체를 dict로 반환한다."""
    import os
    os.environ["AIP_PROVIDER_ANTHROPIC_STUB_MODE"] = "echo"
    provider = AnthropicStubProvider()

    # usage 객체 Mock
    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 50
    usage.cache_read_input_tokens = 30
    usage.cache_creation_input_tokens = 0

    # AnthropicStubProvider에는 _extract_usage 없음 — AnthropicLLMProvider 직접 테스트
    # stub은 _extract_usage 미구현 → 실 클래스에서 테스트
    # 여기서는 함수 로직을 직접 검증 (실 SDK 없이)
    from src.infrastructure.providers.llm.anthropic import AnthropicLLMProvider

    # _extract_usage 는 인스턴스 메서드지만 self 사용 안 함 → 직접 바인딩
    # ProviderUnavailableError 없이 호출하기 위해 object.__new__ 사용
    obj = object.__new__(AnthropicLLMProvider)

    result = obj._extract_usage(usage)

    assert isinstance(result, dict), "_extract_usage 반환 타입은 dict"
    assert "input_tokens" in result
    assert "output_tokens" in result
    assert "cache_read_input_tokens" in result
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 50
    assert result["cache_read_input_tokens"] == 30


def test_extract_usage_none_returns_empty_dict():
    """usage=None 시 빈 dict 반환 (throw 없음)."""
    from src.infrastructure.providers.llm.anthropic import AnthropicLLMProvider
    obj = object.__new__(AnthropicLLMProvider)

    result = obj._extract_usage(None)
    assert result == {}, "None usage → 빈 dict"


def test_extract_usage_cache_creation_included_when_nonzero():
    """cache_creation_input_tokens > 0 시 결과 dict에 포함."""
    from src.infrastructure.providers.llm.anthropic import AnthropicLLMProvider
    obj = object.__new__(AnthropicLLMProvider)

    usage = MagicMock()
    usage.input_tokens = 200
    usage.output_tokens = 60
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 500

    result = obj._extract_usage(usage)
    assert "cache_creation_input_tokens" in result
    assert result["cache_creation_input_tokens"] == 500


def test_extract_usage_cache_creation_omitted_when_zero():
    """cache_creation_input_tokens == 0 시 결과 dict에서 생략."""
    from src.infrastructure.providers.llm.anthropic import AnthropicLLMProvider
    obj = object.__new__(AnthropicLLMProvider)

    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 40
    usage.cache_read_input_tokens = 10
    usage.cache_creation_input_tokens = 0

    result = obj._extract_usage(usage)
    # 0이면 생략
    assert "cache_creation_input_tokens" not in result


def test_extract_usage_partial_attrs():
    """usage 객체에 일부 attr 없어도 KeyError/AttributeError 없이 0 처리."""
    from src.infrastructure.providers.llm.anthropic import AnthropicLLMProvider
    obj = object.__new__(AnthropicLLMProvider)

    # output_tokens 없는 최소 usage
    usage = MagicMock(spec=["input_tokens"])
    usage.input_tokens = 50

    result = obj._extract_usage(usage)
    assert isinstance(result, dict)
    assert result["input_tokens"] == 50
    assert result["output_tokens"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# 테스트 4: workflow done 봉투 기존 필드 유지 확인
# ──────────────────────────────────────────────────────────────────────────────

async def test_workflow_done_preserves_existing_fields():
    """_stream_workflow done 봉투의 기존 workflow{} 필드가 유지된다."""
    from src.agent.graph_executor import GraphExecutor
    from src.router.graph_cache import GraphCache
    from src.domain.models import AgentMode, SearchScope
    from src.router.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType
    from src.workflow.engine import StepResult

    mock_llm = AsyncMock()
    mock_registry = MagicMock()

    # mock workflow engine
    mock_engine = AsyncMock()
    mock_engine.get_session = AsyncMock(return_value=None)

    step = StepResult(
        bot_message="안녕하세요!",
        options=["선택 A", "선택 B"],
        step_id="s1",
        step_type="option",
        collected={"name": "테스터"},
        completed=False,
        escaped=False,
        report=None,
        intent_confirm=None,
        collection=None,
        concluded=False,
    )
    mock_engine.start = AsyncMock(return_value=step)

    executor = GraphExecutor(
        main_llm=mock_llm,
        tool_registry=mock_registry,
        guardrails={},
        chat_model=MagicMock(),
        workflow_engine=mock_engine,
        graph_cache=GraphCache(),
    )

    plan = ExecutionPlan(
        mode=AgentMode.WORKFLOW,
        scope=SearchScope(),
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=False),
        tool_groups=[],
        system_prompt="",
        workflow_id="test_wf",
    )

    events = []
    async for event in executor.execute_stream("안녕", plan, "sess-wf"):
        events.append(event)

    done_events = [e for e in events if e.get("type") == "done"]
    assert len(done_events) == 1

    wf_data = done_events[0]["data"]
    # 기존 workflow{} 키 보존
    assert "workflow" in wf_data, "workflow{} 키 보존"
    wf = wf_data["workflow"]
    assert wf["options"] == ["선택 A", "선택 B"]
    assert wf["step_id"] == "s1"
    assert wf["step_type"] == "option"
    assert wf["collected"] == {"name": "테스터"}
    assert wf["completed"] is False
    assert wf["concluded"] is False
