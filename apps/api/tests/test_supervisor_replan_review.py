"""Supervisor P1-1 adaptive replan + P1-4 메인 검토 게이트 테스트.

두 기능 모두 opt-in(SupervisorLimits 플래그) — 기본값(off)에서는 어떤 추가 LLM
호출도 없어야 한다(기존 스위트가 그 회귀를 보장). 여기서는 켰을 때의 계약을 본다.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.agent_profile import AgentProfile
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult, SupervisorLimits
from src.supervisor.planner_llm import SupervisorPlanner
from src.supervisor.supervisor import Supervisor


def _profiles(*ids: str) -> list[AgentProfile]:
    return [AgentProfile(id=pid, name=pid, description=f"{pid} 설명") for pid in ids]


def _make_supervisor(profiles, planner, limits, runner_run=None):
    runner = AsyncMock()

    async def _default_run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        return SubAgentResult(profile=profile_id, answer=f"{profile_id} 답변", ok=True)

    runner.run = AsyncMock(side_effect=runner_run or _default_run)

    authorizer = AsyncMock()
    authorizer.resolve_allowed.return_value = None
    authorizer.is_delegation_allowed = lambda allowed, pid: True

    profile_store = AsyncMock()
    profile_store.list_all.return_value = profiles

    return Supervisor(planner, runner, authorizer, limits, profile_store), runner


def _ctx():
    return AgentContext(session_id="s1")


def _user_ctx():
    return SimpleNamespace(security_level_max="PUBLIC")


# --- P1-1 adaptive replan ---


@pytest.mark.asyncio
async def test_replan_executes_additional_delegations():
    """replan이 추가 위임을 반환하면 다음 라운드가 실행되고 결과가 종합에 합쳐진다."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p1", subquery="q1")]
    )
    planner.replan.side_effect = [
        DelegationPlan(delegations=[DelegationStep(profile="p2", subquery="q2-추가")]),
        DelegationPlan(delegations=[]),
    ]
    planner.synthesize.return_value = "종합 답변"

    limits = SupervisorLimits(adaptive_replan=True, max_replan_rounds=2)
    sup, runner = _make_supervisor(_profiles("p1", "p2"), planner, limits)

    resp = await sup.supervise("질문", _ctx(), _user_ctx())

    assert runner.run.await_count == 2
    executed = [call.args[0] for call in runner.run.await_args_list]
    assert executed == ["p1", "p2"]
    # synthesize는 두 라운드의 결과를 모두 받는다.
    results = planner.synthesize.call_args.args[1]
    assert [r.profile for r in results] == ["p1", "p2"]
    assert resp.answer == "종합 답변"


@pytest.mark.asyncio
async def test_replan_empty_plan_finalizes_without_extra_round():
    """replan이 빈 계획을 반환하면 추가 위임 없이 바로 종합한다."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p1", subquery="q1")]
    )
    planner.replan.return_value = DelegationPlan(delegations=[])
    planner.synthesize.return_value = "종합 답변"

    limits = SupervisorLimits(adaptive_replan=True, max_replan_rounds=1)
    sup, runner = _make_supervisor(_profiles("p1", "p2"), planner, limits)

    await sup.supervise("질문", _ctx(), _user_ctx())

    assert runner.run.await_count == 1
    planner.replan.assert_awaited_once()


@pytest.mark.asyncio
async def test_replan_rounds_capped_by_max_replan_rounds():
    """replan이 계속 위임을 만들어도 max_replan_rounds에서 멈춘다(무한 라운드 방지)."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p1", subquery="q1")]
    )
    # 호출될 때마다 새 위임을 반환하는 적대적 replan
    planner.replan.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p2", subquery="계속 추가")]
    )
    planner.synthesize.return_value = "종합 답변"

    limits = SupervisorLimits(adaptive_replan=True, max_replan_rounds=1, max_delegations=10)
    sup, runner = _make_supervisor(_profiles("p1", "p2"), planner, limits)

    await sup.supervise("질문", _ctx(), _user_ctx())

    # 1라운드(decompose) + 1 replan 라운드 = 위임 2회에서 종료.
    assert runner.run.await_count == 2
    assert planner.replan.await_count == 1


@pytest.mark.asyncio
async def test_replan_total_delegations_respect_budget_cap():
    """replan 라운드도 총 위임 캡(max_delegations)을 공유한다."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
        ]
    )
    planner.replan.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p3", subquery="q3")]
    )
    planner.synthesize.return_value = "종합 답변"

    # 캡 2 = 1라운드에서 소진 → replan 라운드로 못 간다.
    limits = SupervisorLimits(adaptive_replan=True, max_replan_rounds=3, max_delegations=2)
    sup, runner = _make_supervisor(_profiles("p1", "p2", "p3"), planner, limits)

    await sup.supervise("질문", _ctx(), _user_ctx())

    assert runner.run.await_count == 2
    planner.replan.assert_not_awaited()  # 예산 소진 → replan 노드 미방문


@pytest.mark.asyncio
async def test_replan_skipped_when_disabled():
    """adaptive_replan=False(기본)면 planner.replan이 절대 호출되지 않는다."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p1", subquery="q1")]
    )
    planner.synthesize.return_value = "종합 답변"

    sup, _ = _make_supervisor(_profiles("p1"), planner, SupervisorLimits())

    await sup.supervise("질문", _ctx(), _user_ctx())

    planner.replan.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_replan_never_redelegates_same_profile():
    """SupervisorPlanner.replan은 이미 위임한 프로파일을 코드 레벨에서 걸러낸다."""
    llm = AsyncMock()
    llm.generate_json.return_value = {
        "delegations": [
            {"profile": "p1", "subquery": "같은 서브 반복", "reason": "중복"},
            {"profile": "p2", "subquery": "새 도메인", "reason": "누락 도메인"},
        ]
    }
    planner = SupervisorPlanner(llm)
    prior = [SubAgentResult(profile="p1", answer="1차 답변", ok=True)]
    candidates = [{"id": "p1", "name": "p1"}, {"id": "p2", "name": "p2"}]

    plan = await planner.replan("질문", prior, candidates)
    assert [d.profile for d in plan.delegations] == ["p2"]


# --- P1-4 메인 검토 게이트 ---


@pytest.mark.asyncio
async def test_review_gate_excludes_rejected_results_from_synthesis():
    """reject된 서브 답변은 ok=False로 강등되어 통과분만 종합된다."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
        ]
    )

    async def review(question, result):
        if result.profile == "p2":
            return {"passed": False, "note": "질의와 무관한 답변"}
        return {"passed": True, "note": "정상"}

    planner.review.side_effect = review
    planner.synthesize.return_value = "종합 답변"

    limits = SupervisorLimits(review_gate=True)
    sup, _ = _make_supervisor(_profiles("p1", "p2"), planner, limits)

    resp = await sup.supervise("질문", _ctx(), _user_ctx())

    assert planner.review.await_count == 2
    results = planner.synthesize.call_args.args[1]
    by_profile = {r.profile: r for r in results}
    assert by_profile["p1"].ok is True and by_profile["p1"].review_passed is True
    assert by_profile["p2"].ok is False and by_profile["p2"].error == "review_rejected"
    # 판정 내역이 운영자 트레이스에 남는다.
    review_trace = resp.trace.router_decision["review"]
    assert {e["profile"]: e["passed"] for e in review_trace} == {"p1": True, "p2": False}


@pytest.mark.asyncio
async def test_review_gate_fail_open_on_reviewer_error():
    """판정 LLM이 죽어도 답변은 유실되지 않는다(fail-open)."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p1", subquery="q1")]
    )
    planner.review.side_effect = RuntimeError("reviewer down")
    planner.synthesize.return_value = "종합 답변"

    limits = SupervisorLimits(review_gate=True)
    sup, _ = _make_supervisor(_profiles("p1"), planner, limits)

    resp = await sup.supervise("질문", _ctx(), _user_ctx())

    results = planner.synthesize.call_args.args[1]
    assert results[0].ok is True  # fail-open — 게이트 장애가 답변 유실로 번지지 않는다
    assert resp.answer == "종합 답변"


@pytest.mark.asyncio
async def test_review_gate_skipped_when_disabled():
    """review_gate=False(기본)면 planner.review가 절대 호출되지 않는다."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p1", subquery="q1")]
    )
    planner.synthesize.return_value = "종합 답변"

    sup, _ = _make_supervisor(_profiles("p1"), planner, SupervisorLimits())

    await sup.supervise("질문", _ctx(), _user_ctx())

    planner.review.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_gate_does_not_touch_workflow_handoff_passthrough():
    """핸드오프 passthrough(단계 질문)는 검토 게이트를 타지 않는다 — 훼손 금지."""

    async def handoff_run(profile_id, *args, **kwargs):
        return SubAgentResult(
            profile=profile_id, answer="생년월일시를 알려주세요",
            ok=True, workflow_handoff=True,
        )

    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="fortune-saju", subquery="사주")]
    )

    limits = SupervisorLimits(review_gate=True)
    sup, _ = _make_supervisor(_profiles("fortune-saju"), planner, limits, runner_run=handoff_run)

    resp = await sup.supervise("내 사주좀 봐줘", _ctx(), _user_ctx())

    assert resp.answer == "생년월일시를 알려주세요"
    planner.review.assert_not_awaited()
    planner.synthesize.assert_not_awaited()
