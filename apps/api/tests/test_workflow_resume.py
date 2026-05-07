"""WorkflowEngine.resume() 단위 테스트."""

import pytest
from unittest.mock import MagicMock

from src.workflow.engine import WorkflowEngine, StepResult
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.common.exceptions import GatewayError


@pytest.fixture
def store():
    s = MagicMock()
    return s


@pytest.fixture
def engine(store):
    return WorkflowEngine(store)


def _make_definition(wf_id="test-wf"):
    return WorkflowDefinition(
        id=wf_id,
        name="테스트 워크플로우",
        steps=[
            WorkflowStep(id="step_1", type="input", prompt="이름을 알려주세요.", save_as="name", next="step_2"),
            WorkflowStep(id="step_2", type="input", prompt="{{name}}님, 전화번호를 알려주세요.", save_as="phone", next="step_3"),
            WorkflowStep(id="step_3", type="select", prompt="보험 유형을 선택하세요.", options=["자동차", "건강"], branches={"자동차": "step_4", "건강": "step_4"}, save_as="type"),
            WorkflowStep(id="step_4", type="confirm", prompt="입력 정보를 확인해주세요."),
        ],
    )


async def test_resume_from_step_2(engine, store):
    """step_2에서 재개한다."""
    definition = _make_definition()
    store.get.return_value = definition

    result = await engine.resume(
        workflow_id="test-wf",
        session_id="sess-1",
        step_id="step_2",
        collected={"name": "김"},
    )

    assert result.step_id == "step_2"
    assert result.step_type == "input"
    assert "김님" in result.bot_message
    assert result.collected == {"name": "김"}


async def test_resume_from_step_3(engine, store):
    """step_3 (select)에서 재개한다."""
    definition = _make_definition()
    store.get.return_value = definition

    result = await engine.resume(
        workflow_id="test-wf",
        session_id="sess-1",
        step_id="step_3",
        collected={"name": "김", "phone": "010-1234-5678"},
    )

    assert result.step_id == "step_3"
    assert result.step_type == "select"
    assert "자동차" in result.options


async def test_resume_creates_session(engine, store):
    """resume 후 세션이 메모리에 존재한다."""
    store.get.return_value = _make_definition()

    await engine.resume("test-wf", "sess-2", "step_2", {"name": "박"})

    session = await engine.get_session("sess-2")
    assert session is not None
    assert session.workflow_id == "test-wf"
    assert session.current_step_id == "step_2"
    assert session.collected == {"name": "박"}


async def test_resume_then_advance(engine, store):
    """resume 후 advance로 다음 스텝으로 진행한다."""
    store.get.return_value = _make_definition()

    await engine.resume("test-wf", "sess-3", "step_2", {"name": "이"})
    result = await engine.advance("sess-3", "010-9999-8888")

    assert result.step_id == "step_3"
    assert result.step_type == "select"

    session = await engine.get_session("sess-3")
    assert session.collected["phone"] == "010-9999-8888"


async def test_resume_workflow_not_found(engine, store):
    """워크플로우를 찾을 수 없으면 GatewayError."""
    store.get.return_value = None

    with pytest.raises(GatewayError, match="워크플로우를 찾을 수 없습니다"):
        await engine.resume("nonexistent", "sess-1", "step_1", {})


async def test_resume_step_not_found(engine, store):
    """스텝을 찾을 수 없으면 GatewayError."""
    store.get.return_value = _make_definition()

    with pytest.raises(GatewayError, match="스텝을 찾을 수 없습니다"):
        await engine.resume("test-wf", "sess-1", "nonexistent-step", {})


async def test_resume_replaces_existing_session(engine, store):
    """기존 세션이 있어도 resume으로 덮어쓴다."""
    store.get.return_value = _make_definition()

    await engine.resume("test-wf", "sess-4", "step_1", {})
    await engine.resume("test-wf", "sess-4", "step_3", {"name": "최", "phone": "010"})

    session = await engine.get_session("sess-4")
    assert session.current_step_id == "step_3"
    assert session.collected == {"name": "최", "phone": "010"}
