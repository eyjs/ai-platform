"""Phase 3: 오케스트레이터 → Supervisor 통합(컷오버) 테스트.

컷오버 후 chatbot_id 미지정(자동 라우팅)은 무조건 supervisor가 흡수하고
(라우팅 = 1위임의 특수케이스), 직접 모드는 불가침(§0-1)임을 검증한다.
단일 위임 passthrough(라우팅 파리티)도 여기서 본다.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.agent_profile import AgentProfile
from src.gateway.routes.helpers import _is_supervisor_request
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult, SupervisorLimits
from src.supervisor.supervisor import Supervisor


def _make_state(supervisor=None, supervisor_profile_id: str = "supervisor"):
    settings = SimpleNamespace(
        supervisor_profile_id=supervisor_profile_id,
        default_tenant_id="default",
    )
    return SimpleNamespace(settings=settings, supervisor=supervisor)


# --- 엔트리 판별 (Phase 3 흡수 규칙) ---


def test_auto_routing_absorbed_by_supervisor():
    """컷오버: chatbot_id 미지정 → supervisor 분기 (무조건)."""
    state = _make_state(supervisor=AsyncMock())
    assert _is_supervisor_request(None, state) is True


def test_direct_mode_never_absorbed():
    """직접 모드(특정 chatbot_id)는 절대 흡수되지 않는다(§0-1)."""
    state = _make_state(supervisor=AsyncMock())
    assert _is_supervisor_request("insurance-qa", state) is False
    assert _is_supervisor_request("fortune-saju", state) is False


def test_auto_routing_not_absorbed_when_supervisor_not_wired():
    """supervisor 미배선이면 안전 폴백(False) — 직접 모드 헬퍼가 400으로 거절한다."""
    state = _make_state(supervisor=None)
    assert _is_supervisor_request(None, state) is False


def test_supervisor_explicit_entry_works():
    """chatbot_id=supervisor 명시 진입 계약 유지."""
    state = _make_state(supervisor=AsyncMock())
    assert _is_supervisor_request("supervisor", state) is True


# --- 단일 위임 passthrough (라우팅 파리티) ---


def _profiles(*ids: str) -> list[AgentProfile]:
    return [AgentProfile(id=pid, name=pid, description=f"{pid} 설명") for pid in ids]


def _make_supervisor(profiles, planner, limits, runner_run=None):
    runner = AsyncMock()

    async def _default_run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        return SubAgentResult(
            profile=profile_id, answer=f"{profile_id} 원문 답변",
            sources=[{"document_id": "d1", "title": "문서1"}], ok=True,
        )

    runner.run = AsyncMock(side_effect=runner_run or _default_run)

    authorizer = AsyncMock()
    authorizer.resolve_allowed.return_value = None
    authorizer.is_delegation_allowed = lambda allowed, pid: True

    profile_store = AsyncMock()
    profile_store.list_all.return_value = profiles

    return Supervisor(planner, runner, authorizer, limits, profile_store)


def _ctx():
    return AgentContext(session_id="s1")


def _user_ctx():
    return SimpleNamespace(security_level_max="PUBLIC")


@pytest.mark.asyncio
async def test_single_passthrough_skips_synthesize():
    """단일 위임 성공 + 플래그 on → 서브 답변·출처 그대로, synthesize 미호출."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="insurance-qa", subquery="q")]
    )

    limits = SupervisorLimits(single_passthrough=True)
    sup = _make_supervisor(_profiles("insurance-qa"), planner, limits)

    resp = await sup.supervise("질문", _ctx(), _user_ctx())

    assert resp.answer == "insurance-qa 원문 답변"
    assert len(resp.sources) == 1 and resp.sources[0].document_id == "d1"
    planner.synthesize.assert_not_awaited()
    # 트레이스는 여전히 위임 경로를 노출한다(운영자 transparent).
    assert resp.trace.router_decision["delegations"][0]["profile"] == "insurance-qa"


@pytest.mark.asyncio
async def test_single_passthrough_off_by_default_synthesizes():
    """플래그 off(기본)면 단일 위임도 기존대로 synthesize를 거친다(P0 계약 유지)."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="insurance-qa", subquery="q")]
    )
    planner.synthesize.return_value = "종합 답변"

    sup = _make_supervisor(_profiles("insurance-qa"), planner, SupervisorLimits())

    resp = await sup.supervise("질문", _ctx(), _user_ctx())

    assert resp.answer == "종합 답변"
    planner.synthesize.assert_awaited_once()


@pytest.mark.asyncio
async def test_single_passthrough_not_applied_to_multi_delegation():
    """다중 위임은 플래그와 무관하게 synthesize로 종합한다."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
        ]
    )
    planner.synthesize.return_value = "종합 답변"

    limits = SupervisorLimits(single_passthrough=True)
    sup = _make_supervisor(_profiles("p1", "p2"), planner, limits)

    resp = await sup.supervise("질문", _ctx(), _user_ctx())

    assert resp.answer == "종합 답변"
    planner.synthesize.assert_awaited_once()


@pytest.mark.asyncio
async def test_single_passthrough_not_applied_to_failed_delegation():
    """단일 위임이 실패하면 passthrough하지 않고 degrade 종합(폴백 문구) 경로를 탄다."""

    async def failing_run(profile_id, *args, **kwargs):
        return SubAgentResult(profile=profile_id, answer="", ok=False, error="boom")

    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p1", subquery="q")]
    )
    planner.synthesize.return_value = "폴백 종합"

    limits = SupervisorLimits(single_passthrough=True)
    sup = _make_supervisor(_profiles("p1"), planner, limits, runner_run=failing_run)

    resp = await sup.supervise("질문", _ctx(), _user_ctx())

    assert resp.answer == "폴백 종합"
    planner.synthesize.assert_awaited_once()


@pytest.mark.asyncio
async def test_single_passthrough_blocked_by_review_rejection():
    """검토 게이트가 reject한 단일 결과는 passthrough되지 못한다(게이트 우회 금지)."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p1", subquery="q")]
    )
    planner.review.return_value = {"passed": False, "note": "질의와 무관"}
    planner.synthesize.return_value = "폴백 종합"

    limits = SupervisorLimits(single_passthrough=True, review_gate=True)
    sup = _make_supervisor(_profiles("p1"), planner, limits)

    resp = await sup.supervise("질문", _ctx(), _user_ctx())

    assert resp.answer == "폴백 종합"
    planner.synthesize.assert_awaited_once()
