"""T4: WorkflowEngine start()/resume() 경로에서 _save_session 단일 호출 검증."""

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
    return WorkflowEngine(store)


async def test_start_calls_save_session_once(engine: WorkflowEngine):
    """start()는 _process_current_step 완료 후 _save_session을 1회만 호출한다."""
    with patch.object(engine, "_save_session", new_callable=AsyncMock) as mock_save:
        await engine.start(workflow_id="wf-1", session_id="sess-1")

        assert mock_save.await_count == 1, (
            f"_save_session이 {mock_save.await_count}회 호출됨 (expected 1)"
        )


async def test_resume_calls_save_session_once(engine: WorkflowEngine):
    """resume()은 _process_current_step 완료 후 _save_session을 1회만 호출한다."""
    with patch.object(engine, "_save_session", new_callable=AsyncMock) as mock_save:
        await engine.resume(
            workflow_id="wf-1",
            session_id="sess-1",
            step_id="s1",
            collected={},
        )

        assert mock_save.await_count == 1, (
            f"_save_session이 {mock_save.await_count}회 호출됨 (expected 1)"
        )


async def test_start_saves_after_step_processing(engine: WorkflowEngine):
    """start()의 _save_session은 _process_current_step 이후에 호출된다."""
    call_order: list[str] = []

    original_process = engine._process_current_step

    async def tracked_process(*args, **kwargs):
        call_order.append("process")
        return await original_process(*args, **kwargs)

    async def tracked_save(session_id, session):
        call_order.append("save")
        # 인메모리 저장 수행
        engine._sessions[session_id] = session

    with (
        patch.object(engine, "_process_current_step", side_effect=tracked_process),
        patch.object(engine, "_save_session", side_effect=tracked_save),
    ):
        await engine.start(workflow_id="wf-1", session_id="sess-1")

    assert call_order == ["process", "save"], (
        f"호출 순서가 잘못됨: {call_order} (expected ['process', 'save'])"
    )


async def test_resume_saves_after_step_processing(engine: WorkflowEngine):
    """resume()의 _save_session은 _process_current_step 이후에 호출된다."""
    call_order: list[str] = []

    original_process = engine._process_current_step

    async def tracked_process(*args, **kwargs):
        call_order.append("process")
        return await original_process(*args, **kwargs)

    async def tracked_save(session_id, session):
        call_order.append("save")
        engine._sessions[session_id] = session

    with (
        patch.object(engine, "_process_current_step", side_effect=tracked_process),
        patch.object(engine, "_save_session", side_effect=tracked_save),
    ):
        await engine.resume(
            workflow_id="wf-1",
            session_id="sess-1",
            step_id="s1",
            collected={},
        )

    assert call_order == ["process", "save"], (
        f"호출 순서가 잘못됨: {call_order} (expected ['process', 'save'])"
    )


async def test_advance_still_calls_save_once(engine: WorkflowEngine, store: MagicMock):
    """advance()도 기존대로 _save_session 1회만 호출한다 (회귀 방지)."""
    store.get.return_value = _make_definition()

    # 먼저 start로 세션 생성
    await engine.start(workflow_id="wf-1", session_id="sess-adv")

    with patch.object(engine, "_save_session", new_callable=AsyncMock) as mock_save:
        await engine.advance(session_id="sess-adv", user_input="홍길동")

        assert mock_save.await_count == 1, (
            f"advance에서 _save_session이 {mock_save.await_count}회 호출됨 (expected 1)"
        )
