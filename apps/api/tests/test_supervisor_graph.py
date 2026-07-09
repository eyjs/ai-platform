"""Supervisor StateGraph 전환(P1-0) + 병렬 fan-out(P1-2) 구조 테스트.

행동 계약(관문·캡·hub·sticky·핸드오프)은 기존 스위트가 공개 API로 검증하므로,
여기서는 그래프 전환 자체가 보장해야 하는 것만 본다:
1) 토폴로지 — 설계문서 §7의 노드/엣지 구성이 실제 그래프에 존재.
2) 실행 경로 — 일반 경로/sticky 경로가 의도한 노드 순서로만 흐른다(astream 관측).
3) 병렬성 — 한 라운드의 위임들이 동시에 실행된다(Send fan-out).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.agent_profile import AgentProfile
from src.supervisor.graph import build_supervisor_graph
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult, SupervisorLimits


def _profiles(*ids: str) -> list[AgentProfile]:
    return [AgentProfile(id=pid, name=pid, description=f"{pid} 설명") for pid in ids]


def _make_deps(profiles, delegations, allowed=None, workflow_engine=None, runner_run=None):
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(delegations=delegations)
    planner.synthesize.return_value = "종합 답변"

    runner = AsyncMock()

    async def _default_run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        return SubAgentResult(profile=profile_id, answer=f"{profile_id} 답변", ok=True)

    runner.run = AsyncMock(side_effect=runner_run or _default_run)

    authorizer = AsyncMock()
    authorizer.resolve_allowed.return_value = allowed

    def _is_allowed(allowed_set, profile_id):
        return True if allowed_set is None else profile_id in allowed_set

    authorizer.is_delegation_allowed = _is_allowed

    profile_store = AsyncMock()
    profile_store.list_all.return_value = profiles

    return planner, runner, authorizer, profile_store, workflow_engine


def _build(profiles, delegations, allowed=None, workflow_engine=None, limits=None, runner_run=None):
    planner, runner, authorizer, profile_store, engine = _make_deps(
        profiles, delegations, allowed, workflow_engine, runner_run
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
        "round": 0,
        "budget": None,
        "current_step": None,
        "results": [],
        "delegation_log": [],
        "response": None,
    }


async def _visited_nodes(graph, state) -> list[str]:
    """astream으로 실행 경로(노드 방문 순서)를 관측한다."""
    visited = []
    async for chunk in graph.astream(state, stream_mode="updates"):
        visited.extend(chunk.keys())
    return visited


def test_graph_topology_matches_design():
    """설계문서 §7의 노드 8개가 컴파일된 그래프에 전부 존재한다."""
    graph = _build(_profiles("p1"), [])
    nodes = set(graph.get_graph().nodes.keys())
    expected = {
        "resolve_scope", "detect_sticky", "sticky_delegate",
        "decompose", "delegate", "collect", "replan", "finalize",
    }
    assert expected <= nodes, f"누락 노드: {expected - nodes}"


@pytest.mark.asyncio
async def test_normal_path_visits_delegate_per_step_then_finalize():
    """일반 경로: resolve → sticky감지 → decompose → delegate×N(fan-out) → collect → finalize."""
    graph = _build(
        _profiles("p1", "p2"),
        [
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
        ],
    )
    visited = await _visited_nodes(graph, _initial_state())
    assert visited == [
        "resolve_scope", "detect_sticky", "decompose",
        "delegate", "delegate", "collect", "finalize",
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
async def test_cap_exhaustion_dispatches_within_budget_only():
    """예산(max_delegations=1)을 넘는 계획은 dispatch에서 잘린다(무한 루프 없음)."""
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


@pytest.mark.asyncio
async def test_parallel_delegations_run_concurrently():
    """P1-2: 한 라운드의 위임 2건이 실제로 동시에 실행된다.

    두 서브가 서로의 시작을 기다리는 rendezvous — 순차 실행이면 첫 위임이
    상대를 영원히 기다리다 delegation_timeout으로 실패한다. 병렬이면 둘 다
    즉시 만나서 ok=True로 완료된다.
    """
    first_started = asyncio.Event()
    second_started = asyncio.Event()

    async def rendezvous_run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        if profile_id == "p1":
            first_started.set()
            await asyncio.wait_for(second_started.wait(), timeout=2)
        else:
            second_started.set()
            await asyncio.wait_for(first_started.wait(), timeout=2)
        return SubAgentResult(profile=profile_id, answer=f"{profile_id} 답변", ok=True)

    planner, runner, authorizer, profile_store, _ = _make_deps(
        _profiles("p1", "p2"),
        [
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
        ],
        runner_run=rendezvous_run,
    )
    graph = build_supervisor_graph(
        planner=planner, runner=runner, authorizer=authorizer,
        limits=SupervisorLimits(delegation_timeout_sec=5.0),
        profile_store=profile_store,
    )

    final = await asyncio.wait_for(graph.ainvoke(_initial_state()), timeout=10)

    results = final["results"]
    assert len(results) == 2
    assert all(r.ok for r in results), [r.error for r in results]
    # reducer 적용 순서는 Send 생성 순서(=계획 순서)로 결정적이다.
    assert [r.profile for r in results] == ["p1", "p2"]


@pytest.mark.asyncio
async def test_delegation_log_records_every_delegation():
    """P1-3: 위임 경로 전부가 delegation_log와 응답 trace에 남는다(운영자 transparent)."""
    graph = _build(
        _profiles("p1", "p2"),
        [
            DelegationStep(profile="p1", subquery="q1", reason="r1"),
            DelegationStep(profile="p2", subquery="q2", reason="r2"),
        ],
    )
    final = await graph.ainvoke(_initial_state())

    log = final["delegation_log"]
    assert [e["profile"] for e in log] == ["p1", "p2"]
    assert all(e["ok"] for e in log)
    assert all(e["latency_ms"] >= 0 for e in log)

    trace = final["response"].trace
    assert trace is not None and trace.mode == "supervisor"
    assert [e["profile"] for e in trace.router_decision["delegations"]] == ["p1", "p2"]
