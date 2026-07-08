"""Supervisor 최소 위임 루프 단위 테스트 (task-003, P0-3).

planner/runner/authorizer/limits를 모두 fake로 주입해 decompose → 위임 → synthesize
루프의 계약(관문 위치, 캡, hub 강제)을 검증한다.
"""

from unittest.mock import AsyncMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.agent_profile import AgentProfile
from src.supervisor.limits import DelegationBudget
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult, SupervisorLimits
from src.supervisor.supervisor import Supervisor


class _FakeUserCtx:
    """resolve_allowed/is_delegation_allowed가 요구하는 최소 사용자 컨텍스트."""

    def __init__(self, security_level_max: str = "PUBLIC"):
        self.security_level_max = security_level_max


def _profiles(*ids: str) -> list[AgentProfile]:
    return [AgentProfile(id=pid, name=pid, description=f"{pid} 설명") for pid in ids]


def _make_authorizer(allowed):
    """resolve_allowed는 고정 allowed 집합을 반환하고, is_delegation_allowed는
    실제 구현(allowed None이면 전체 허용, 아니면 in 검사)과 동일하게 동작하는 fake."""
    authorizer = AsyncMock()
    authorizer.resolve_allowed.return_value = allowed

    def _is_allowed(allowed_set, profile_id):
        if allowed_set is None:
            return True
        return profile_id in allowed_set

    authorizer.is_delegation_allowed = _is_allowed
    return authorizer


def _make_runner(result_map: dict[str, SubAgentResult] | None = None):
    runner = AsyncMock()

    async def _run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None):
        if result_map and profile_id in result_map:
            return result_map[profile_id]
        return SubAgentResult(profile=profile_id, answer=f"{profile_id} 답변", ok=True)

    runner.run = AsyncMock(side_effect=_run)
    return runner


def _make_profile_store(profiles: list[AgentProfile]):
    store = AsyncMock()
    store.list_all.return_value = profiles
    return store


def _make_planner(plan: DelegationPlan, synthesize_answer: str = "종합 답변"):
    planner = AsyncMock()
    planner.decompose.return_value = plan
    planner.synthesize.return_value = synthesize_answer
    return planner


@pytest.mark.asyncio
async def test_multi_domain_synthesis():
    """멀티도메인 종합(DoD §7-3): 2위임 → 두 서브 run 호출, synthesize가 종합 응답 생성."""
    profiles = _profiles("insurance-qa", "kms-assistant")
    plan = DelegationPlan(
        delegations=[
            DelegationStep(profile="insurance-qa", subquery="보험 질의"),
            DelegationStep(profile="kms-assistant", subquery="문서 질의"),
        ]
    )
    planner = _make_planner(plan)
    runner = _make_runner()
    authorizer = _make_authorizer(None)
    limits = SupervisorLimits(max_delegations=4, max_depth=1)
    profile_store = _make_profile_store(profiles)

    supervisor = Supervisor(planner, runner, authorizer, limits, profile_store)
    ctx = AgentContext(session_id="s1", tenant_id="tenant-a")
    user_ctx = _FakeUserCtx()

    resp = await supervisor.supervise("보험이랑 문서 둘 다 알려줘", ctx, user_ctx)

    assert runner.run.await_count == 2
    called_profiles = {call.args[0] for call in runner.run.await_args_list}
    assert called_profiles == {"insurance-qa", "kms-assistant"}

    planner.synthesize.assert_awaited_once()
    _, kwargs = planner.synthesize.call_args
    results = planner.synthesize.call_args.args[1]
    assert len(results) == 2
    assert resp.answer == "종합 답변"


@pytest.mark.asyncio
async def test_deny_by_default_skips_out_of_scope_profile():
    """deny-by-default(DoD §7-2): allowed 밖 프로파일이 계획에 섞이면 스킵, runner.run 미호출."""
    profiles = _profiles("insurance-qa")
    plan = DelegationPlan(
        delegations=[
            DelegationStep(profile="forbidden-profile", subquery="금지된 질의"),
        ]
    )
    planner = _make_planner(plan)
    runner = _make_runner()
    authorizer = _make_authorizer({"insurance-qa"})  # forbidden-profile은 allowed 밖
    limits = SupervisorLimits(max_delegations=4, max_depth=1)
    profile_store = _make_profile_store(profiles)

    supervisor = Supervisor(planner, runner, authorizer, limits, profile_store)
    ctx = AgentContext(session_id="s1")
    user_ctx = _FakeUserCtx()

    resp = await supervisor.supervise("질문", ctx, user_ctx)

    runner.run.assert_not_awaited()
    planner.synthesize.assert_awaited_once()
    results = planner.synthesize.call_args.args[1]
    assert results == []


@pytest.mark.asyncio
async def test_cap_truncates_delegations_without_infinite_loop():
    """캡(DoD §7-4): max_delegations=1인데 계획이 3위임 → 1회만 실행 후 중단."""
    profiles = _profiles("p1", "p2", "p3")
    plan = DelegationPlan(
        delegations=[
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
            DelegationStep(profile="p3", subquery="q3"),
        ]
    )
    planner = _make_planner(plan)
    runner = _make_runner()
    authorizer = _make_authorizer(None)
    limits = SupervisorLimits(max_delegations=1, max_depth=1)
    profile_store = _make_profile_store(profiles)

    supervisor = Supervisor(planner, runner, authorizer, limits, profile_store)
    ctx = AgentContext(session_id="s1")
    user_ctx = _FakeUserCtx()

    resp = await supervisor.supervise("질문", ctx, user_ctx)

    assert runner.run.await_count == 1
    results = planner.synthesize.call_args.args[1]
    assert len(results) == 1


@pytest.mark.asyncio
async def test_hub_loop_only_iterates_plan_delegations():
    """hub(DoD §7-7): 루프가 plan.delegations만 순회, 서브 결과로 재위임 대상을 정하지 않는다.

    SubAgentResult에는 next 계열 필드가 없어(계약상 불가) fake runner가 재위임을
    유도할 방법이 없음을 확인한다. 또한 3위임 계획 → run이 정확히 3회만 호출됨을
    검증해, 서브 결과 기반 추가 위임이 발생하지 않음을 보인다.
    """
    for forbidden in ("next_profile", "route_to", "next_step", "delegate_to"):
        assert not hasattr(SubAgentResult(profile="p", answer="a"), forbidden)

    profiles = _profiles("p1", "p2", "p3")
    plan = DelegationPlan(
        delegations=[
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
            DelegationStep(profile="p3", subquery="q3"),
        ]
    )
    planner = _make_planner(plan)
    runner = _make_runner()
    authorizer = _make_authorizer(None)
    limits = SupervisorLimits(max_delegations=10, max_depth=1)
    profile_store = _make_profile_store(profiles)

    supervisor = Supervisor(planner, runner, authorizer, limits, profile_store)
    ctx = AgentContext(session_id="s1")
    user_ctx = _FakeUserCtx()

    await supervisor.supervise("질문", ctx, user_ctx)

    assert runner.run.await_count == 3


@pytest.mark.asyncio
async def test_decompose_parse_failure_falls_back_to_single_delegation():
    """decompose 파싱 실패 → 단일 위임 폴백으로 안전 종료."""
    profiles = _profiles("general")
    # planner.decompose 자체가 폴백 계획(단일 위임)을 반환하는 상황을 시뮬레이션.
    fallback_plan = DelegationPlan(
        delegations=[DelegationStep(profile="general", subquery="질문", reason="decompose 폴백")]
    )
    planner = _make_planner(fallback_plan)
    runner = _make_runner()
    authorizer = _make_authorizer(None)
    limits = SupervisorLimits(max_delegations=4, max_depth=1)
    profile_store = _make_profile_store(profiles)

    supervisor = Supervisor(planner, runner, authorizer, limits, profile_store)
    ctx = AgentContext(session_id="s1")
    user_ctx = _FakeUserCtx()

    resp = await supervisor.supervise("애매한 질문", ctx, user_ctx)

    runner.run.assert_awaited_once()
    assert runner.run.await_args.args[0] == "general"
    results = planner.synthesize.call_args.args[1]
    assert len(results) == 1
    assert results[0].ok is True


@pytest.mark.asyncio
async def test_budget_consume_matches_can_delegate_contract():
    """DelegationBudget 계약(can_delegate 선검사 후 consume) 회귀 확인용 스모크 테스트."""
    limits = SupervisorLimits(max_delegations=1, max_depth=1)
    budget = DelegationBudget(limits)
    assert budget.can_delegate() is True
    budget.consume()
    assert budget.can_delegate() is False
    with pytest.raises(RuntimeError):
        budget.consume()
