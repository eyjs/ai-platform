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


def _make_hint(name, patterns, description=""):
    h = MagicMock()
    h.name = name
    h.patterns = patterns
    h.description = description
    return h


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
async def test_route_continuation_short_question(orchestrator, deps):
    """짧은 후속 질문 (<15자)은 현재 프로필을 유지한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [_make_profile("food-recipe")]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {"current_profile_id": "food-recipe"}
    sm.get_turns.return_value = [{"role": "user", "content": "김치찌개 레시피"}]

    result = await orchestrator.route("칼로리 얼마야?", "sess-1", FakeUserCtx())

    assert result.is_continuation
    assert result.selected_profile_id == "food-recipe"


@pytest.mark.asyncio
async def test_route_continuation_past_reference(orchestrator, deps):
    """'아까' + 키워드로 과거 프로필을 참조한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [
        _make_profile("food-recipe", domain_scopes=["요리", "레시피"],
                      intent_hints=[_make_hint("RECIPE", ["레시피", "요리법"])]),
        _make_profile("insurance-qa", domain_scopes=["자동차보험"]),
    ]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {
        "current_profile_id": "insurance-qa",
        "profile_history": [
            {"profile_id": "food-recipe", "switched_at": 1000},
            {"profile_id": "insurance-qa", "switched_at": 2000},
        ],
    }
    sm.get_turns.return_value = [{"role": "user", "content": "보험료 알려줘"}]

    result = await orchestrator.route("아까 레시피에서 두부 넣어도 돼?", "sess-1", FakeUserCtx())

    assert result.selected_profile_id == "food-recipe"
    llm.select_profile.assert_not_called()


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
async def test_route_tier1_keyword_match(orchestrator, deps):
    """Tier 1: intent_hints 키워드로 LLM 없이 프로필을 선택한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [
        _make_profile("food-recipe", name="요리",
                      intent_hints=[_make_hint("RECIPE", ["레시피", "만드는 법"])]),
        _make_profile("insurance-qa", name="보험"),
    ]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    result = await orchestrator.route("김치찌개 레시피 알려줘", "sess-1", FakeUserCtx())

    assert result.selected_profile_id == "food-recipe"
    assert "Tier 1" in result.reason
    llm.select_profile.assert_not_called()


@pytest.mark.asyncio
async def test_route_tier1_greeting(orchestrator, deps):
    """Tier 1: 인사를 general-chat으로 라우팅한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [
        _make_profile("general-chat", name="일반"),
        _make_profile("insurance-qa", name="보험"),
    ]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    result = await orchestrator.route("안녕하세요", "sess-1", FakeUserCtx())

    assert result.selected_profile_id == "general-chat"
    assert "Tier 1" in result.reason
    llm.select_profile.assert_not_called()


@pytest.mark.asyncio
async def test_route_tier2_domain_scope(orchestrator, deps):
    """Tier 2: domain_scopes 키워드로 프로필을 선택한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [
        _make_profile("general-chat", name="일반"),
        _make_profile("insurance-qa", name="보험",
                      domain_scopes=["자동차보험", "실손보험"]),
    ]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    result = await orchestrator.route("자동차보험 보장 내용이 뭐야?", "sess-1", FakeUserCtx())

    assert result.selected_profile_id == "insurance-qa"
    assert "Tier" in result.reason
    llm.select_profile.assert_not_called()


@pytest.mark.asyncio
async def test_route_tier3_llm_fallback(orchestrator, deps):
    """Tier 3: 키워드 매칭 실패 시 LLM으로 폴백한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [
        _make_profile("general-chat", name="일반"),
        _make_profile("insurance-qa", name="보험"),
    ]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    llm.select_profile.return_value = {
        "function": "select_profile",
        "profile_id": "insurance-qa",
        "reason": "보험 관련 질문",
    }

    result = await orchestrator.route("내 건강이 걱정되는데 어떤 상품이 좋을까", "sess-1", FakeUserCtx())

    assert result.selected_profile_id == "insurance-qa"
    assert "Tier 3" in result.reason
    llm.select_profile.assert_called_once()


@pytest.mark.asyncio
async def test_route_tier3_no_tool_call_fallback(orchestrator, deps):
    """Tier 3: tool_calls 없으면 텍스트 추출 또는 폴백한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [
        _make_profile("general-chat", name="일반"),
        _make_profile("food-recipe", name="요리"),
    ]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    llm.select_profile.return_value = {
        "function": "no_tool_call",
        "text": "food-recipe 프로필이 적합합니다",
        "profile_id": "",
        "reason": "tool_calls 없음",
    }

    result = await orchestrator.route("뭔가 맛있는 거 먹고 싶어", "sess-1", FakeUserCtx())

    # no_tool_call 시 텍스트 추출 대신 Tier2 재시도 또는 폴백
    # "맛있는" 키워드가 food-recipe의 intent_hints에 없으므로 general-chat 폴백
    assert result.selected_profile_id in ("food-recipe", "general-chat")


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
    """LLM 오류 시 현재 프로필 -> 첫 번째 프로필로 폴백한다."""
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
async def test_route_tier3_retries_tier2(orchestrator, deps):
    """Tier 3: LLM tool_calls 없을 때 Tier 2를 min_score=0.3으로 재시도한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [
        _make_profile("general-chat", name="일반"),
        _make_profile("insurance-qa", name="보험",
                      domain_scopes=["자동차보험", "실손보험"],
                      intent_hints=[_make_hint("INS", ["보험", "보장"])]),
    ]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    # LLM이 tool_calls 없이 응답
    llm.select_profile.return_value = {
        "function": "no_tool_call",
        "text": "잘 모르겠습니다",
        "profile_id": "",
        "reason": "",
    }

    result = await orchestrator.route("보험 상품 알려줘", "sess-1", FakeUserCtx())

    # Tier 2 재시도로 insurance-qa 매칭 기대
    assert result.selected_profile_id == "insurance-qa"
    assert "Tier 2 재시도" in result.reason or "Tier" in result.reason


@pytest.mark.asyncio
async def test_route_tier3_fallback_general_chat(orchestrator, deps):
    """Tier 3: 모든 매칭 실패 시 general-chat으로 폴백한다."""
    llm, ps, sm, we, ts = deps

    ps.list_all.return_value = [
        _make_profile("general-chat", name="일반"),
        _make_profile("food-recipe", name="요리",
                      intent_hints=[_make_hint("RECIPE", ["레시피"])]),
    ]
    ts.get_allowed_profiles.return_value = []
    sm.get_orchestrator_metadata.return_value = {}
    sm.get_turns.return_value = []

    # LLM이 tool_calls 없이 응답, 프로필 추출도 실패
    llm.select_profile.return_value = {
        "function": "no_tool_call",
        "text": "잘 모르겠습니다",
        "profile_id": "",
        "reason": "",
    }

    result = await orchestrator.route("인생이 힘들어", "sess-1", FakeUserCtx())

    assert result.selected_profile_id == "general-chat"


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
