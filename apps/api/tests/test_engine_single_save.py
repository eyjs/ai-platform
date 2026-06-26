"""T6: WorkflowEngine LangGraph ainvoke 단일 호출 검증.

T6 단일 엔진 컷오버 후 LangGraph 경로만 남았다.
start()/resume()/advance()가 각각 graph.ainvoke를 1회만 호출함을 검증한다.
MemorySaver를 checkpointer로 사용해 DB 없이 인메모리로 동작한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workflow.engine import WorkflowEngine, StepResult
from src.workflow.definition import WorkflowDefinition, WorkflowStep


def _make_definition(wf_id: str = "wf-1") -> WorkflowDefinition:
    return WorkflowDefinition(
        id=wf_id,
        name="테스트",
        steps=[
            WorkflowStep(
                id="s1",
                type="input",
                prompt="이름?",
                save_as="name",
                next="s2",
            ),
            WorkflowStep(
                id="s2",
                type="input",
                prompt="번호?",
                save_as="phone",
            ),
        ],
    )


@pytest.fixture
def store() -> MagicMock:
    s = MagicMock()
    s.get.return_value = _make_definition()
    return s


@pytest.fixture
def engine(store: MagicMock) -> WorkflowEngine:
    # graph_builder/checkpointer 미주입 시 MemorySaver + WorkflowGraphBuilder 자동 생성
    return WorkflowEngine(store)


async def test_start_calls_ainvoke_once(engine: WorkflowEngine):
    """start()는 graph.ainvoke를 1회만 호출한다."""
    graph_mock = MagicMock()
    graph_mock.ainvoke = AsyncMock(return_value={})
    graph_mock.aget_state = AsyncMock(return_value=MagicMock(
        values={"last_result": {"bot_message": "이름?", "step_id": "s1", "step_type": "input"}},
        next=None,
        tasks=[],
    ))

    with patch.object(engine._graph_builder, "get_graph", return_value=graph_mock):
        await engine.start(workflow_id="wf-1", session_id="sess-1")

    assert graph_mock.ainvoke.await_count == 1, (
        f"ainvoke가 {graph_mock.ainvoke.await_count}회 호출됨 (expected 1)"
    )


async def test_resume_calls_ainvoke_once(engine: WorkflowEngine):
    """resume()은 graph.ainvoke를 1회만 호출한다."""
    graph_mock = MagicMock()
    graph_mock.ainvoke = AsyncMock(return_value={})
    graph_mock.aget_state = AsyncMock(return_value=MagicMock(
        values={"last_result": {"bot_message": "이름?", "step_id": "s1", "step_type": "input"}},
        next=None,
        tasks=[],
    ))

    with patch.object(engine._graph_builder, "get_graph", return_value=graph_mock):
        await engine.resume(
            workflow_id="wf-1",
            session_id="sess-1",
            step_id="s1",
            collected={},
        )

    assert graph_mock.ainvoke.await_count == 1, (
        f"ainvoke가 {graph_mock.ainvoke.await_count}회 호출됨 (expected 1)"
    )


async def test_advance_calls_ainvoke_once(engine: WorkflowEngine):
    """advance()도 graph.ainvoke를 1회만 호출한다 (회귀 방지)."""
    from langgraph.checkpoint.memory import MemorySaver
    from src.workflow.graph_builder import WorkflowGraphBuilder

    # advance()는 먼저 _lg_get_session으로 현재 세션을 확인한 후 ainvoke를 호출한다.
    # 세션이 있는 상태를 만들기 위해 먼저 start()로 세션을 생성한 뒤 graph_mock으로 교체한다.
    checkpointer = MemorySaver()
    builder = WorkflowGraphBuilder(store=engine._store)
    real_engine = WorkflowEngine(engine._store, graph_builder=builder, checkpointer=checkpointer)

    # start로 세션 생성 (실 LangGraph 실행)
    await real_engine.start(workflow_id="wf-1", session_id="sess-adv")

    # advance 실행 — graph.ainvoke 호출 횟수 검증
    graph_mock = MagicMock()
    graph_mock.ainvoke = AsyncMock(return_value={})
    graph_mock.aget_state = AsyncMock(return_value=MagicMock(
        values={"last_result": {"bot_message": "번호?", "step_id": "s2", "step_type": "input"}},
        next=None,
        tasks=[],
    ))

    with patch.object(real_engine._graph_builder, "get_graph", return_value=graph_mock):
        await real_engine.advance(session_id="sess-adv", user_input="홍길동")

    assert graph_mock.ainvoke.await_count == 1, (
        f"advance에서 ainvoke가 {graph_mock.ainvoke.await_count}회 호출됨 (expected 1)"
    )


async def test_start_returns_step_result(engine: WorkflowEngine):
    """start()는 StepResult를 반환한다."""
    graph_mock = MagicMock()
    graph_mock.ainvoke = AsyncMock(return_value={})
    graph_mock.aget_state = AsyncMock(return_value=MagicMock(
        values={"last_result": {
            "bot_message": "이름?",
            "step_id": "s1",
            "step_type": "input",
            "collected": {},
            "completed": False,
        }},
        next=None,
        tasks=[],
    ))

    with patch.object(engine._graph_builder, "get_graph", return_value=graph_mock):
        result = await engine.start(workflow_id="wf-1", session_id="sess-2")

    assert isinstance(result, StepResult)
    assert result.bot_message == "이름?"
    assert result.step_id == "s1"


async def test_resume_returns_step_result(engine: WorkflowEngine):
    """resume()은 StepResult를 반환한다."""
    graph_mock = MagicMock()
    graph_mock.ainvoke = AsyncMock(return_value={})
    graph_mock.aget_state = AsyncMock(return_value=MagicMock(
        values={"last_result": {
            "bot_message": "이름?",
            "step_id": "s1",
            "step_type": "input",
            "collected": {"existing": "value"},
            "completed": False,
        }},
        next=None,
        tasks=[],
    ))

    with patch.object(engine._graph_builder, "get_graph", return_value=graph_mock):
        result = await engine.resume(
            workflow_id="wf-1",
            session_id="sess-3",
            step_id="s1",
            collected={"existing": "value"},
        )

    assert isinstance(result, StepResult)
    assert result.step_id == "s1"
