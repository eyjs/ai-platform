"""MasterOrchestrator 단위 테스트."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.models import OrchestratorResult
from src.orchestrator.orchestrator import MasterOrchestrator


class FakeUserCtx:
    allowed_profiles = []
    tenant_id = None


def _make_profile(pid, name="test", description="", domain_scopes=None, intent_hints=None):
    p = MagicMock()
    p.id = pid
    p.name = name
    p.description = description
    p.domain_scopes = domain_scopes or []
    p.intent_hints = intent_hints or []
    return p


@pytest.fixture
def deps():
    llm = AsyncMock()
    profile_store = AsyncMock()
    session_memory = AsyncMock()
    workflow_engine = MagicMock()
    tenant_service = AsyncMock()
    return llm, profile_store, session_memory, workflow_engine, tenant_service


@pytest.fixture
def orchestrator(deps):
    llm, ps, sm, we, ts = deps
    return MasterOrchestrator(
        llm=llm,
        profile_store=ps,
        session_memory=sm,
        workflow_engine=we,
        tenant_service=ts,
    )


@pytest.mark.asyncio
async def test_route_continuation_connective(orchestrator, deps):
    """연속 표현('그리고')으로 시작하면 현재 프로필을 유지한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [_make_profile("insurance-qa")]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {"current_profile_id": "insurance-qa"}
    sm.get_turns.return_value = [{"role": "user", "content": "보험료 알려줘"}]

    result = await orchestrator.route("그리고 보장 범위도 알려줘", "sess-1", FakeUserCtx())

    assert result.is_continuation
    assert result.selected_profile_id == "insurance-qa"
    llm.select_profile.assert_not_called()


@pytest.mark.asyncio
async def test_route_continuation_pronoun(orchestrator, deps):
    """대명사 포함 질문은 현재 프로필을 유지한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [_make_profile("insurance-qa")]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {"current_profile_id": "insurance-qa"}
    sm.get_turns.return_value = [{"role": "user", "content": "보험료 알려줘"}]

    result = await orchestrator.route("이거 자세히 설명해줘", "sess-1", FakeUserCtx())

    assert result.is_continuation
    assert result.selected_profile_id == "insurance-qa"


@pytest.mark.asyncio
async def test_route_resume_workflow(orchestrator, deps):
    """워크플로우 재개 키워드를 감지한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [_make_profile("insurance-contract")]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {
        "current_profile_id": "insurance-contract",
        "paused_workflow": {
            "workflow_id": "contract-wf",
            "step_id": "step_3",
            "collected": {"name": "김"},
            "profile_id": "insurance-contract",
        },
    }
    sm.get_turns.return_value = []

    result = await orchestrator.route("다시 계약 이어서", "sess-1", FakeUserCtx())

    assert result.should_resume_workflow
    assert result.selected_profile_id == "insurance-contract"
    assert result.paused_state["step_id"] == "step_3"


@pytest.mark.asyncio
async def test_route_llm_select_profile(orchestrator, deps):
    """LLM이 프로필을 선택한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [
        _make_profile("insurance-qa", name="보험 Q&A"),
        _make_profile("fee-calc", name="수수료 계산"),
    ]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    llm.select_profile.return_value = {
        "function": "select_profile",
        "profile_id": "insurance-qa",
        "reason": "보험 관련 질문",
    }

    result = await orchestrator.route("삼성 종신보험 보장 내용 알려줘", "sess-1", FakeUserCtx())

    assert result.selected_profile_id == "insurance-qa"
    assert not result.is_general_response
    llm.select_profile.assert_called_once()


@pytest.mark.asyncio
async def test_route_llm_general_response(orchestrator, deps):
    """LLM이 일반 응답을 선택한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [_make_profile("insurance-qa")]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    llm.select_profile.return_value = {
        "function": "general_response",
        "message": "안녕하세요!",
    }

    result = await orchestrator.route("안녕하세요", "sess-1", FakeUserCtx())

    assert result.is_general_response
    assert result.general_message == "안녕하세요!"


@pytest.mark.asyncio
async def test_route_no_profiles(orchestrator, deps):
    """프로필이 없으면 일반 응답을 반환한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = []
    ts.get_allowed_profiles.return_value = []

    result = await orchestrator.route("보험료 알려줘", "sess-1", FakeUserCtx())

    assert result.is_general_response
    assert "사용 가능한 서비스" in result.general_message


@pytest.mark.asyncio
async def test_route_llm_error_fallback(orchestrator, deps):
    """LLM 오류 시 첫 번째 프로필로 폴백한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [_make_profile("fallback-qa")]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    llm.select_profile.side_effect = RuntimeError("API error")

    result = await orchestrator.route("보험료 알려줘", "sess-1", FakeUserCtx())

    assert result.selected_profile_id == "fallback-qa"


@pytest.mark.asyncio
async def test_route_tenant_filter(orchestrator, deps):
    """테넌트 필터링이 동작한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [
        _make_profile("insurance-qa"),
        _make_profile("fee-calc"),
        _make_profile("contract"),
    ]
    # 테넌트에는 insurance-qa만 허용
    ts.get_allowed_profiles.return_value = ["insurance-qa"]
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    user_ctx = FakeUserCtx()
    user_ctx.tenant_id = "tenant-b"

    llm.select_profile.return_value = {
        "function": "select_profile",
        "profile_id": "insurance-qa",
        "reason": "유일한 프로필",
    }

    result = await orchestrator.route("보험료 알려줘", "sess-1", user_ctx)

    assert result.selected_profile_id == "insurance-qa"
    # LLM에 전달된 프로필이 1개인지 확인
    call_args = llm.select_profile.call_args
    profiles_arg = call_args[0][1]  # 두 번째 인자 (profiles)
    assert len(profiles_arg) == 1
    assert profiles_arg[0]["id"] == "insurance-qa"


@pytest.mark.asyncio
async def test_handle_switch_pauses_workflow(orchestrator, deps):
    """프로필 전환 시 활성 워크플로우를 일시정지한다."""
    llm, ps, sm, we, ts = deps

    active_wf = MagicMock()
    active_wf.completed = False
    active_wf.workflow_id = "contract-wf"
    active_wf.current_step_id = "step_2"
    active_wf.collected = {"name": "김"}

    we.get_session.return_value = active_wf
    we.cancel.return_value = True

    meta = {"current_profile_id": "insurance-contract"}
    await orchestrator._handle_switch("sess-1", "insurance-contract", meta)

    assert "paused_workflow" in meta
    assert meta["paused_workflow"]["workflow_id"] == "contract-wf"
    we.cancel.assert_called_once_with("sess-1")
    sm.save_orchestrator_metadata.assert_called_once()
