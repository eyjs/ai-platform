"""Supervisor StateGraph 전환(P1-0) 구조 테스트.

행동 계약(관문·캡·hub·sticky·핸드오프)은 기존 스위트가 공개 API로 검증하므로,
여기서는 그래프 전환 자체가 보장해야 하는 것만 본다:
1) 토폴로지 — 설계문서 §7 Phase 1.5의 노드/엣지 구성이 실제 그래프에 존재.
2) 실행 경로 — 일반 경로/sticky 경로가 의도한 노드 순서로만 흐른다(astream 관측).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.agent_profile import AgentProfile
from src.supervisor.graph import build_supervisor_graph
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult, SupervisorLimits


def _profiles(*ids: str) -> list[AgentProfile]:
    return [AgentProfile(id=pid, name=pid, description=f"{pid} 설명") for pid in ids]


def _make_deps(profiles, delegations, allowed=None, workflow_engine=None):
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(delegations=delegations)
    planner.synthesize.return_value = "종합 답변"

    runner = AsyncMock()

    async def _run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        return SubAgentResult(profile=profile_id, answer=f"{profile_id} 답변", ok=True)

    runner.run = AsyncMock(side_effect=_run)

    authorizer = AsyncMock()
    authorizer.resolve_allowed.return_value = allowed

    def _is_allowed(allowed_set, profile_id):
        return True if allowed_set is None else profile_id in allowed_set

    authorizer.is_delegation_allowed = _is_allowed

    profile_store = AsyncMock()
    profile_store.list_all.return_value = profiles

    return planner, runner, authorizer, profile_store, workflow_engine


def _build(profiles, delegations, allowed=None, workflow_engine=None, limits=None):
    planner, runner, authorizer, profile_store, engine = _make_deps(
        profiles, delegations, allowed, workflow_engine
    )
    graph = build_supervisor_graph(
        planner=planner,
        runner=runner,
        authorizer=authorizer,
        limits=limits or SupervisorLimits(),
        profile_store=profile_store,
        workflow_engine=engine,
    )
    return graph


def _initial_state(question="질문", session_id="s1"):
    return {
        "question": question,
        "ctx": AgentContext(session_id=session_id),
        "user_ctx": SimpleNamespace(security_level_max="PUBLIC"),
        "trace": None,
        "allowed": None,
        "all_profiles": [],
        "candidates": [],
        "supervisor_id": "",
        "sticky_profile": None,
        "plan": None,
        "workflow_policy": "block",
        "step_index": 0,
        "budget": None,
        "results": [],
        "response": None,
    }


async def _visited_nodes(graph, state) -> list[str]:
    """astream으로 실행 경로(노드 방문 순서)를 관측한다."""
    visited = []
    async for chunk in graph.astream(state, stream_mode="updates"):
        visited.extend(chunk.keys())
    return visited


def test_graph_topology_matches_design():
    """설계문서 §7 Phase 1.5의 노드 6개가 컴파일된 그래프에 전부 존재한다."""
    graph = _build(_profiles("p1"), [])
    nodes = set(graph.get_graph().nodes.keys())
    expected = {"resolve_scope", "detect_sticky", "sticky_delegate", "decompose", "delegate", "finalize"}
    assert expected <= nodes, f"누락 노드: {expected - nodes}"


@pytest.mark.asyncio
async def test_normal_path_visits_delegate_per_step_then_finalize():
    """일반 경로: resolve → sticky감지 → decompose → delegate×N(순차 루프) → finalize."""
    graph = _build(
        _profiles("p1", "p2"),
        [
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
        ],
    )
    visited = await _visited_nodes(graph, _initial_state())
    assert visited == [
        "resolve_scope", "detect_sticky", "decompose", "delegate", "delegate", "finalize",
    ]


@pytest.mark.asyncio
async def test_sticky_path_bypasses_decompose_nodes():
    """sticky 경로: decompose/delegate/finalize 노드를 아예 방문하지 않는다."""
    engine = AsyncMock()

    async def get_session(session_id):
        if session_id.endswith("::sub::fortune-saju"):
            return SimpleNamespace(completed=False)
        return None

    engine.get_session.side_effect = get_session

    graph = _build(
        _profiles("insurance-qa", "fortune-saju"), [], workflow_engine=engine,
    )
    visited = await _visited_nodes(graph, _initial_state(question="1990년 5월 5일"))
    assert visited == ["resolve_scope", "detect_sticky", "sticky_delegate"]


@pytest.mark.asyncio
async def test_cap_exhaustion_routes_to_finalize_not_loop():
    """예산 소진 시 delegate self-loop이 finalize로 빠진다(무한 루프 없음)."""
    graph = _build(
        _profiles("p1", "p2", "p3"),
        [
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
            DelegationStep(profile="p3", subquery="q3"),
        ],
        limits=SupervisorLimits(max_delegations=1),
    )
    visited = await _visited_nodes(graph, _initial_state())
    assert visited.count("delegate") == 1
    assert visited[-1] == "finalize"
