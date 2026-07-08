"""Supervisor 워크플로우 위임 가드 + 위임 타임아웃 회귀 테스트.

실사고(2026-07-08): supervisor로 "내 사주좀 봐줘" → fortune-saju가 workflow
모드(saju_discovery)로 라우팅 → 인터랙티브 워크플로우가 stateless 위임과 불일치
→ 응답 없이 행, SSE ping만 무한 반복.

가드 2겹:
1) runner: plan.mode == WORKFLOW 이면 실행하지 않고 ok=False 명시 반환 (구조적 차단)
2) supervisor: 위임 1건당 delegation_timeout_sec 상한 (행 일반 방어)
+ degrade UX: 워크플로우 차단으로 전부 실패 시 "해당 챗봇 직접 이용" 안내.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.agent_profile import AgentProfile
from src.domain.models import AgentMode
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult, SupervisorLimits
from src.supervisor.subagent_runner import SubAgentRunner
from src.supervisor.supervisor import Supervisor


def _profile(profile_id: str, name: str = "") -> AgentProfile:
    return AgentProfile(id=profile_id, name=name or profile_id)


# --- 1) runner: workflow 모드 위임 차단 ---


@pytest.mark.asyncio
async def test_runner_blocks_workflow_mode_delegation():
    """plan.mode == WORKFLOW 이면 agent.execute를 호출하지 않고 ok=False 반환."""
    profile_store = AsyncMock()
    profile_store.get.return_value = _profile("fortune-saju", "사주팔자 상담사")

    ai_router = AsyncMock()
    ai_router.route.return_value = SimpleNamespace(
        mode=AgentMode.WORKFLOW, workflow_id="saju_discovery",
    )

    agent = AsyncMock()
    tool_registry = AsyncMock()
    tool_registry.resolve = lambda tool_names: []

    runner = SubAgentRunner(
        profile_store=profile_store, ai_router=ai_router,
        agent=agent, tool_registry=tool_registry,
    )
    result = await runner.run(
        "fortune-saju", "내 사주좀 봐줘",
        AgentContext(session_id="s1"),
        user_security_level="PUBLIC", tenant_id="default",
    )

    assert result.ok is False
    assert result.error == "workflow_delegation_unsupported"
    agent.execute.assert_not_called()  # 워크플로우 서브는 실행 자체가 없어야 한다


@pytest.mark.asyncio
async def test_runner_handoff_policy_executes_workflow():
    """workflow_policy='handoff'면 워크플로우를 실행하고 handoff 표식으로 반환."""
    from src.domain.models import AgentResponse, TraceInfo

    profile_store = AsyncMock()
    profile_store.get.return_value = _profile("fortune-saju", "사주팔자 상담사")

    ai_router = AsyncMock()
    ai_router.route.return_value = SimpleNamespace(
        mode=AgentMode.WORKFLOW, workflow_id="saju_discovery",
    )

    agent = AsyncMock()
    agent.execute.return_value = AgentResponse(
        answer="생년월일시를 알려주세요", sources=[], trace=TraceInfo(mode="workflow"),
    )
    tool_registry = AsyncMock()
    tool_registry.resolve = lambda tool_names: []

    runner = SubAgentRunner(
        profile_store=profile_store, ai_router=ai_router,
        agent=agent, tool_registry=tool_registry,
    )
    result = await runner.run(
        "fortune-saju", "내 사주좀 봐줘",
        AgentContext(session_id="s1"),
        user_security_level="PUBLIC", tenant_id="default",
        workflow_policy="handoff",
    )

    assert result.ok is True
    assert result.workflow_handoff is True
    assert "생년월일시" in result.answer
    agent.execute.assert_called_once()


# --- supervisor 조립 헬퍼 ---


def _make_supervisor(
    runner_run, profiles, delegations,
    timeout_sec: float = 120.0, workflow_engine=None,
):
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(delegations=delegations)
    planner.synthesize.return_value = "종합 답변"

    runner = AsyncMock()
    runner.run.side_effect = runner_run

    authorizer = AsyncMock()
    authorizer.resolve_allowed.return_value = {p.id for p in profiles}
    authorizer.is_delegation_allowed = lambda allowed, pid: pid in allowed

    profile_store = AsyncMock()
    profile_store.list_all.return_value = profiles

    limits = SupervisorLimits(delegation_timeout_sec=timeout_sec)
    sup = Supervisor(
        planner, runner, authorizer, limits, profile_store,
        workflow_engine=workflow_engine,
    )
    return sup, planner


def _user_ctx():
    return SimpleNamespace(security_level_max="PUBLIC", allowed_profiles=["*"])


# --- 2) supervisor: 위임 타임아웃 ---


@pytest.mark.asyncio
async def test_supervisor_delegation_timeout_degrades_not_hangs():
    """서브가 행이면 delegation_timeout_sec 후 ok=False로 수집되고 supervise가 반환된다."""

    async def hanging_run(*args, **kwargs):
        await asyncio.sleep(30)  # 타임아웃(0.05s)보다 훨씬 긴 행 시뮬레이션
        return SubAgentResult(profile="insurance-qa", answer="늦은 답", ok=True)

    profiles = [_profile("insurance-qa", "보험 상담 챗봇")]
    sup, planner = _make_supervisor(
        hanging_run, profiles,
        [DelegationStep(profile="insurance-qa", subquery="q")],
        timeout_sec=0.05,
    )

    resp = await asyncio.wait_for(
        sup.supervise("질문", AgentContext(session_id="s1"), _user_ctx()),
        timeout=5,  # supervise 자체가 행이면 여기서 실패
    )

    # 타임아웃된 위임은 실패로 수집되어 synthesize(degrade)로 흘렀어야 한다
    synth_results = planner.synthesize.call_args.args[1]
    assert len(synth_results) == 1
    assert synth_results[0].ok is False
    assert synth_results[0].error == "delegation_timeout"
    assert resp.answer  # 빈 응답 금지


# --- 3) degrade UX: 워크플로우 차단 전부 실패 → 직접 이용 안내 ---


@pytest.mark.asyncio
async def test_supervisor_workflow_blocked_returns_guidance():
    """워크플로우 차단으로 전부 실패하면 해당 챗봇 직접 이용 안내를 반환한다."""

    async def blocked_run(profile_id, *args, **kwargs):
        return SubAgentResult(
            profile=profile_id, answer="", ok=False,
            error="workflow_delegation_unsupported",
        )

    profiles = [_profile("fortune-saju", "사주팔자 상담사")]
    sup, planner = _make_supervisor(
        blocked_run, profiles,
        [DelegationStep(profile="fortune-saju", subquery="내 사주좀 봐줘")],
    )

    resp = await sup.supervise("내 사주좀 봐줘", AgentContext(session_id="s1"), _user_ctx())

    assert "사주팔자 상담사" in resp.answer  # 어느 챗봇으로 가야 하는지 명시
    assert "직접" in resp.answer
    planner.synthesize.assert_not_called()  # 일반 폴백이 아니라 안내 경로


# --- 4) 단일 위임 워크플로우 핸드오프: passthrough (synthesize 금지) ---


@pytest.mark.asyncio
async def test_supervisor_single_workflow_delegation_passthrough():
    """단일 위임이 워크플로우 핸드오프면 단계 질문을 그대로 전달한다."""

    async def handoff_run(profile_id, *args, **kwargs):
        assert kwargs.get("workflow_policy") == "handoff"  # 단일 위임 → 핸드오프 정책
        return SubAgentResult(
            profile=profile_id, answer="생년월일시를 알려주세요",
            ok=True, workflow_handoff=True,
        )

    profiles = [_profile("fortune-saju", "사주팔자 상담사")]
    sup, planner = _make_supervisor(
        handoff_run, profiles,
        [DelegationStep(profile="fortune-saju", subquery="내 사주좀 봐줘")],
    )

    resp = await sup.supervise("내 사주좀 봐줘", AgentContext(session_id="s1"), _user_ctx())

    assert resp.answer == "생년월일시를 알려주세요"  # passthrough — 훼손 금지
    planner.synthesize.assert_not_called()


# --- 5) sticky: 진행 중 워크플로우 감지 → decompose 없이 그 서브로 직행 ---


@pytest.mark.asyncio
async def test_supervisor_sticky_resumes_active_workflow():
    """활성 서브 워크플로우가 있으면 decompose를 건너뛰고 재위임한다(멀티턴 연속성)."""

    async def resume_run(profile_id, subquery, *args, **kwargs):
        assert profile_id == "fortune-saju"
        assert kwargs.get("workflow_policy") == "handoff"
        return SubAgentResult(
            profile=profile_id, answer="1990년 5월 5일생이시군요. 태어난 시간은요?",
            ok=True, workflow_handoff=True,
        )

    engine = AsyncMock()

    async def get_session(session_id):
        # 스코프 세션(parent::sub::fortune-saju)에만 활성 워크플로우 존재
        if session_id.endswith("::sub::fortune-saju"):
            return SimpleNamespace(completed=False)
        return None

    engine.get_session.side_effect = get_session

    profiles = [
        _profile("insurance-qa", "보험 상담 챗봇"),
        _profile("fortune-saju", "사주팔자 상담사"),
    ]
    sup, planner = _make_supervisor(
        resume_run, profiles,
        delegations=[],  # decompose가 호출되면 안 되므로 비워둔다
        workflow_engine=engine,
    )

    resp = await sup.supervise("1990년 5월 5일", AgentContext(session_id="s1"), _user_ctx())

    assert "태어난 시간" in resp.answer
    planner.decompose.assert_not_called()  # sticky가 decompose를 우회했어야 한다
    planner.synthesize.assert_not_called()
