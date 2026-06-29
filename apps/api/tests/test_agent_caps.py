"""P0-4 max_tool_calls 캡 + P0-5 agent_timeout_seconds 타임아웃 테스트.

커버:
  1. 툴 캡 (ainvoke): tool 메시지 > max_tool_calls → partial answer + WARN, no exception
  2. 툴 캡 (ainvoke): GraphRecursionError 발생 → partial answer + WARN, no exception
  3. 툴 캡 (astream): on_tool_start 카운터가 max_tool_calls 초과 → 루프 중단, partial 토큰 보존, WARN, done
  4. 타임아웃 (ainvoke): ainvoke 슬립 중 타임아웃 → 친화적 메시지 + WARN, 예외 없음
  5. 타임아웃 (astream): 스트림 중 슬립 → partial 토큰 보존 + 친화적 close + done, WARN
  6. 회귀: 기본 plan(max_tool_calls=5, timeout=30), 빠른 에이전트 1회 툴 호출 → 정상 full answer

AAA 패턴. 외부 LLM 없음 (doubles only).
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.graph_executor import GraphExecutor
from src.domain.models import AgentMode, SearchScope
from src.domain.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType, ToolCall


# ---------------------------------------------------------------------------
# 헬퍼: fake 메시지 빌더
# ---------------------------------------------------------------------------


def _tool_msg(name: str = "test_tool"):
    """ToolMessage 처럼 보이는 fake 메시지."""
    m = MagicMock()
    m.type = "tool"
    m.name = name
    return m


def _ai_msg(content: str = "부분 답변입니다."):
    """AIMessage 처럼 보이는 fake 메시지."""
    m = MagicMock()
    m.type = "ai"
    m.content = content
    return m


# ---------------------------------------------------------------------------
# 헬퍼: GraphExecutor 빌드 (실제 LLM 없이)
# ---------------------------------------------------------------------------


def _make_executor(chat_model=None) -> GraphExecutor:
    """테스트용 GraphExecutor. _effective_agentic_app 를 직접 monkeypatch 할 수 있도록
    minimal 인자로만 생성한다."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="")
    mock_registry = MagicMock()
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_registry.get.return_value = mock_tool

    return GraphExecutor(
        main_llm=mock_llm,
        tool_registry=mock_registry,
        guardrails={},
        chat_model=chat_model or MagicMock(),
    )


def _agentic_plan(
    max_tool_calls: int = 5,
    agent_timeout_seconds: int = 30,
) -> ExecutionPlan:
    return ExecutionPlan(
        mode=AgentMode.AGENTIC,
        scope=SearchScope(),
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True),
        tool_groups=[[ToolCall("test_tool", {})]],
        system_prompt="You are a test assistant.",
        max_tool_calls=max_tool_calls,
        agent_timeout_seconds=agent_timeout_seconds,
    )


# ---------------------------------------------------------------------------
# 1. 툴 캡 (ainvoke) — tool 메시지 초과
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_cap_ainvoke_messages_exceed(caplog):
    """ainvoke 결과에 tool 메시지가 max_tool_calls 를 초과하면
    partial answer 를 반환하고 WARN 로그가 남으며 예외가 발생하지 않아야 한다."""
    # Arrange
    executor = _make_executor()
    plan = _agentic_plan(max_tool_calls=2)

    # 3개의 tool 메시지 + 마지막 AI 메시지
    fake_msgs = [
        _tool_msg("tool_a"),
        _tool_msg("tool_b"),
        _tool_msg("tool_c"),  # 3 > 2 → 캡 초과
        _ai_msg("부분 답변입니다."),
    ]
    fake_result = {"messages": fake_msgs}

    fake_app = AsyncMock()
    fake_app.ainvoke = AsyncMock(return_value=fake_result)

    with patch.object(executor, "_effective_agentic_app", return_value=fake_app), \
         caplog.at_level(logging.WARNING, logger="src.agent.graph_executor"):

        # Act
        response = await executor._execute_agentic("질문", plan, "sess-1")

    # Assert: 예외 없이 응답이 나왔음
    assert response is not None
    assert response.answer == "부분 답변입니다."
    # tools_called 가 수집됐음
    assert len(response.trace.tools_called) == 3
    # WARN 로그 확인
    cap_logs = [r for r in caplog.records if "agentic_tool_cap_reached" in r.message]
    assert cap_logs, "캡 경고 로그가 남아야 한다"
    assert cap_logs[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# 2. 툴 캡 (ainvoke) — GraphRecursionError 발생
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_cap_ainvoke_graph_recursion_error(caplog):
    """ainvoke 가 GraphRecursionError 를 raise 해도 호출부에 예외를 전파하지 않고
    WARN 로그를 남긴다."""
    from langgraph.errors import GraphRecursionError

    # Arrange
    executor = _make_executor()
    plan = _agentic_plan(max_tool_calls=2)

    fake_app = AsyncMock()
    fake_app.ainvoke = AsyncMock(side_effect=GraphRecursionError("recursion limit"))

    with patch.object(executor, "_effective_agentic_app", return_value=fake_app), \
         caplog.at_level(logging.WARNING, logger="src.agent.graph_executor"):

        # Act — 예외가 전파되면 안 됨
        response = await executor._execute_agentic("질문", plan, "sess-1")

    # Assert: 예외 없이 응답이 나왔음
    assert response is not None
    # WARN 로그 확인 (recursion 경유 cap 경고)
    cap_logs = [r for r in caplog.records if "agentic_tool_cap_reached" in r.message]
    assert cap_logs, "GraphRecursionError 발생 시 캡 경고 로그가 남아야 한다"
    assert cap_logs[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# 3. 툴 캡 (astream) — on_tool_start 초과
# ---------------------------------------------------------------------------


async def _fake_astream_events_cap(messages_input, version, config=None):
    """on_tool_start 3회 → max_tool_calls=2 초과 시뮬레이션.
    토큰도 앞에서 일부 yield 한다."""
    yield {"event": "on_chat_model_stream", "data": {"chunk": _stream_chunk("부분 ")}}
    yield {"event": "on_tool_start", "name": "tool_a", "data": {}}
    yield {"event": "on_tool_end", "name": "tool_a", "data": {}}
    yield {"event": "on_tool_start", "name": "tool_b", "data": {}}
    yield {"event": "on_tool_end", "name": "tool_b", "data": {}}
    # 3번째 on_tool_start: 캡 초과 → 루프가 break 해야 함
    yield {"event": "on_tool_start", "name": "tool_c", "data": {}}
    # 아래는 캡 이후라 도달하지 않아야 함
    yield {"event": "on_chat_model_stream", "data": {"chunk": _stream_chunk("도달하면 안됨")}}


def _stream_chunk(text: str):
    """on_chat_model_stream 청크 fake."""
    chunk = MagicMock()
    chunk.content = text
    chunk.tool_calls = None
    chunk.usage_metadata = None
    return chunk


@pytest.mark.asyncio
async def test_tool_cap_astream_stops_after_max(caplog):
    """astream 루프가 max_tool_calls 초과 on_tool_start 이벤트에서 break 해야 한다.
    이미 yield 된 부분 토큰이 보존되고, WARN 로그가 남으며, done 이벤트가 emit 된다."""
    # Arrange
    executor = _make_executor()
    plan = _agentic_plan(max_tool_calls=2)

    fake_app = MagicMock()
    fake_app.astream_events = _fake_astream_events_cap

    with patch.object(executor, "_effective_agentic_app", return_value=fake_app), \
         caplog.at_level(logging.WARNING, logger="src.agent.graph_executor"):

        # Act
        events = []
        async for event in executor._stream_agentic("질문", plan, "sess-1"):
            events.append(event)

    # Assert: 부분 토큰이 보존됐음
    token_events = [e for e in events if e["type"] == "token"]
    token_text = "".join(e["data"] for e in token_events)
    assert "부분" in token_text
    # 캡 이후 토큰 ("도달하면 안됨")이 포함되지 않았음
    assert "도달하면 안됨" not in token_text

    # done 이벤트 emit 됐음
    done_events = [e for e in events if e["type"] == "done"]
    assert done_events, "done 이벤트가 emit 돼야 한다"

    # WARN 로그 확인
    cap_logs = [r for r in caplog.records if "agentic_tool_cap_reached" in r.message]
    assert cap_logs, "스트리밍 툴 캡 경고 로그가 남아야 한다"
    assert cap_logs[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# 4. 타임아웃 (ainvoke)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_ainvoke_returns_friendly_message(caplog):
    """ainvoke 가 timeout 보다 오래 걸리면 친화적 메시지 + WARN, 예외 없음."""
    # Arrange
    executor = _make_executor()
    plan = _agentic_plan(agent_timeout_seconds=1)  # 1초 타임아웃

    async def _slow_ainvoke(*args, **kwargs):
        await asyncio.sleep(10)  # 10초 → 타임아웃 트리거
        return {"messages": [_ai_msg("이건 도달하면 안됨")]}

    fake_app = MagicMock()
    fake_app.ainvoke = _slow_ainvoke

    with patch.object(executor, "_effective_agentic_app", return_value=fake_app), \
         caplog.at_level(logging.WARNING, logger="src.agent.graph_executor"):

        # Act
        response = await executor._execute_agentic("질문", plan, "sess-1")

    # Assert: 예외 없이 친화적 메시지 반환
    assert response is not None
    assert "지연" in response.answer or "중단" in response.answer  # 한국어 친화적 메시지
    assert "이건 도달하면 안됨" not in response.answer

    # WARN 로그 확인
    timeout_logs = [r for r in caplog.records if "agentic_timeout" in r.message]
    assert timeout_logs, "타임아웃 경고 로그가 남아야 한다"
    assert timeout_logs[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# 5. 타임아웃 (astream)
# ---------------------------------------------------------------------------


async def _fake_astream_events_timeout(messages_input, version, config=None):
    """일부 토큰 yield 후 오래 sleep."""
    yield {"event": "on_chat_model_stream", "data": {"chunk": _stream_chunk("부분 답변")}}
    await asyncio.sleep(10)  # 10초 → 타임아웃 트리거
    yield {"event": "on_chat_model_stream", "data": {"chunk": _stream_chunk("이건 안옴")}}


@pytest.mark.asyncio
async def test_timeout_astream_preserves_partial_tokens(caplog):
    """스트림 중 타임아웃 발생 시 이미 yield 된 부분 토큰이 보존되고,
    친화적 close 토큰 + done 이벤트가 emit 되며, WARN 로그가 남아야 한다."""
    # Arrange
    executor = _make_executor()
    plan = _agentic_plan(agent_timeout_seconds=1)  # 1초 타임아웃

    fake_app = MagicMock()
    fake_app.astream_events = _fake_astream_events_timeout

    with patch.object(executor, "_effective_agentic_app", return_value=fake_app), \
         caplog.at_level(logging.WARNING, logger="src.agent.graph_executor"):

        # Act
        events = []
        async for event in executor._stream_agentic("질문", plan, "sess-1"):
            events.append(event)

    # Assert: 부분 토큰 보존
    token_events = [e for e in events if e["type"] == "token"]
    token_text = "".join(e["data"] for e in token_events)
    assert "부분 답변" in token_text
    # 타임아웃 이후 토큰은 도달하지 않음
    assert "이건 안옴" not in token_text

    # 친화적 close 메시지가 토큰에 포함됐음 (타임아웃 알림)
    assert any("지연" in e["data"] or "중단" in e["data"] for e in token_events if e["type"] == "token"), \
        "타임아웃 close 메시지가 토큰 스트림에 포함돼야 한다"

    # done 이벤트 emit됐음 — 스트림이 열린 채로 남지 않음
    done_events = [e for e in events if e["type"] == "done"]
    assert done_events, "타임아웃 후에도 done 이벤트가 emit 돼야 한다"

    # WARN 로그 확인
    timeout_logs = [r for r in caplog.records if "agentic_stream_timeout" in r.message]
    assert timeout_logs, "스트리밍 타임아웃 경고 로그가 남아야 한다"
    assert timeout_logs[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# 6. 회귀: 기본 plan — 정상 경로가 캡/타임아웃에 걸리지 않아야 한다
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regression_default_plan_normal_execution(caplog):
    """기본 plan (max_tool_calls=5, timeout=30) 에서 1회 툴 호출하는 빠른 에이전트 →
    정상 full answer, 캡/타임아웃 WARN 로그 없음.

    이 테스트는 Task B 의 _effective_agentic_app 오버라이드 seam 을 통해
    fake_app 을 주입하므로 Task B 의 model-override seam 이 여전히 동작함을 암묵적으로 검증한다.
    """
    # Arrange
    executor = _make_executor()
    plan = _agentic_plan(max_tool_calls=5, agent_timeout_seconds=30)

    # 1회 툴 호출 + 최종 AI 답변
    fake_msgs = [
        _tool_msg("tool_a"),
        _ai_msg("완전한 최종 답변입니다."),
    ]
    fake_result = {"messages": fake_msgs}

    fake_app = AsyncMock()
    fake_app.ainvoke = AsyncMock(return_value=fake_result)

    with patch.object(executor, "_effective_agentic_app", return_value=fake_app), \
         caplog.at_level(logging.WARNING, logger="src.agent.graph_executor"):

        # Act
        response = await executor._execute_agentic("질문", plan, "sess-1")

    # Assert: 완전한 답변 반환
    assert response.answer == "완전한 최종 답변입니다."
    assert response.trace.tools_called == ["tool_a"]

    # 캡/타임아웃 WARN 로그가 없어야 함
    warn_logs = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and (
            "agentic_tool_cap_reached" in r.message
            or "agentic_timeout" in r.message
            or "agentic_stream_timeout" in r.message
        )
    ]
    assert not warn_logs, f"정상 경로에서 캡/타임아웃 WARN 로그가 남으면 안 됨: {[r.message for r in warn_logs]}"


# ---------------------------------------------------------------------------
# 7. Task B 모델 오버라이드 seam 이 여전히 동작함을 직접 검증
# ---------------------------------------------------------------------------


@patch("src.agent.executors.agentic_executor.build_agentic_graph")
@patch("src.agent.executors.agentic_executor.convert_tools_to_langchain")
@patch("src.agent.executors.agentic_executor.resolve_model_alias")
@pytest.mark.asyncio
async def test_task_b_model_override_seam_intact(mock_resolve, mock_convert, mock_build, caplog):
    """Task C 패치 후에도 _effective_agentic_app 오버라이드 seam 이 작동해야 한다.
    plan.main_model='sonnet' + stub factory → factory.get_chat_model 이 호출됨."""
    # Arrange
    mock_resolve.return_value = "claude-sonnet-4-5"
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    fake_app = AsyncMock()
    fake_app.ainvoke = AsyncMock(return_value={
        "messages": [_ai_msg("오버라이드 답변")],
    })
    mock_build.return_value = fake_app

    from src.config import Settings
    settings = Settings(provider_mode="anthropic", anthropic_api_key="sk-test")
    factory = MagicMock()
    factory.get_chat_model.return_value = MagicMock()

    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="")
    mock_registry = MagicMock()
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_registry.get.return_value = mock_tool

    from src.agent.graph_executor import GraphExecutor
    executor = GraphExecutor(
        main_llm=mock_llm,
        tool_registry=mock_registry,
        guardrails={},
        chat_model=MagicMock(),
        provider_factory=factory,
        settings=settings,
    )
    plan = ExecutionPlan(
        mode=AgentMode.AGENTIC,
        scope=SearchScope(),
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True),
        tool_groups=[[ToolCall("test_tool", {})]],
        system_prompt="You are a test assistant.",
        main_model="sonnet",
        max_tool_calls=5,
        agent_timeout_seconds=30,
    )

    # Act
    response = await executor.execute("질문", plan, "sess-1")

    # Assert: 오버라이드 factory.get_chat_model 호출됨
    factory.get_chat_model.assert_called_once_with(model_name="claude-sonnet-4-5")
    assert response.answer == "오버라이드 답변"
