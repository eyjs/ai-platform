"""Supervisor 토큰 스트리밍 테스트 (Phase 3 컷오버 선행조건).

supervise_stream의 계약:
- 단일 위임 passthrough 확정(single_passthrough on, replan/review off)이면 서브 토큰이 흐른다.
- 다중 위임이면 synthesize 토큰이 흐른다.
- 버퍼드 경로(워크플로우 핸드오프, deny 폴백)는 토큰 없이 done.streamed=False.
- 어느 경로든 done은 정확히 1건이고 response는 비스트리밍 supervise와 같은 의미다.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.agent_profile import AgentProfile
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult, SupervisorLimits
from src.supervisor.planner_llm import SupervisorPlanner
from src.supervisor.subagent_runner import SubAgentRunner
from src.supervisor.supervisor import Supervisor


def _profiles(*ids: str) -> list[AgentProfile]:
    return [AgentProfile(id=pid, name=pid, description=f"{pid} 설명") for pid in ids]


def _make_supervisor(profiles, planner, limits, runner=None):
    if runner is None:
        runner = AsyncMock()

        async def _default_run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
            return SubAgentResult(profile=profile_id, answer=f"{profile_id} 답변", ok=True)

        runner.run = AsyncMock(side_effect=_default_run)

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


async def _collect(stream):
    events = []
    async for ev in stream:
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_stream_single_passthrough_streams_sub_tokens():
    """단일 위임 passthrough 확정 → 서브 토큰이 실시간으로 흐르고 재방출 없음."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="insurance-qa", subquery="q")]
    )

    runner = AsyncMock()

    async def fake_run_stream(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        yield {"type": "token", "data": "대인배상은 "}
        yield {"type": "token", "data": "사람 피해 보상입니다"}
        yield {"type": "result", "data": SubAgentResult(
            profile=profile_id, answer="대인배상은 사람 피해 보상입니다", ok=True,
        )}

    runner.run_stream = fake_run_stream

    limits = SupervisorLimits(single_passthrough=True)
    sup = _make_supervisor(_profiles("insurance-qa"), planner, limits, runner=runner)

    events = await _collect(sup.supervise_stream("질문", _ctx(), _user_ctx()))

    tokens = [e["data"] for e in events if e["type"] == "token"]
    assert tokens == ["대인배상은 ", "사람 피해 보상입니다"]
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["data"]["streamed"] is True
    assert done[0]["data"]["response"].answer == "대인배상은 사람 피해 보상입니다"
    planner.synthesize.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_multi_delegation_streams_synthesize_tokens():
    """다중 위임 → 서브 토큰은 흐르지 않고 synthesize 토큰이 흐른다."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
        ]
    )

    async def fake_synth_stream(question, results):
        yield "종합 "
        yield "답변"

    planner.synthesize_stream = fake_synth_stream

    limits = SupervisorLimits(single_passthrough=True)  # 다중이라 passthrough 미적용
    sup = _make_supervisor(_profiles("p1", "p2"), planner, limits)

    events = await _collect(sup.supervise_stream("질문", _ctx(), _user_ctx()))

    tokens = [e["data"] for e in events if e["type"] == "token"]
    assert tokens == ["종합 ", "답변"]
    done = events[-1]
    assert done["type"] == "done"
    assert done["data"]["streamed"] is True
    assert done["data"]["response"].answer == "종합 답변"


@pytest.mark.asyncio
async def test_stream_workflow_handoff_is_buffered_not_streamed():
    """워크플로우 핸드오프(단계 질문)는 토큰 없이 done.streamed=False (버퍼드 passthrough)."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="fortune-saju", subquery="사주")]
    )

    runner = AsyncMock()

    async def fake_run_stream(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        assert workflow_policy == "handoff"
        yield {"type": "result", "data": SubAgentResult(
            profile=profile_id, answer="생년월일시를 알려주세요", ok=True, workflow_handoff=True,
        )}

    runner.run_stream = fake_run_stream

    limits = SupervisorLimits(single_passthrough=True)
    sup = _make_supervisor(_profiles("fortune-saju"), planner, limits, runner=runner)

    events = await _collect(sup.supervise_stream("내 사주좀 봐줘", _ctx(), _user_ctx()))

    assert [e["type"] for e in events] == ["done"]
    assert events[0]["data"]["streamed"] is False
    assert events[0]["data"]["response"].answer == "생년월일시를 알려주세요"


@pytest.mark.asyncio
async def test_stream_deny_fallback_streams_fallback_once():
    """후보 0(deny) → synthesize_stream이 폴백 문구를 토큰 1건으로 방출, done 1건."""
    planner = SupervisorPlanner(AsyncMock())  # 실제 planner — 후보 0이면 빈 계획
    limits = SupervisorLimits(single_passthrough=True)

    authorizer = AsyncMock()
    authorizer.resolve_allowed.return_value = set()  # deny-by-default: 아무것도 허용 안 됨
    authorizer.is_delegation_allowed = lambda allowed, pid: pid in allowed

    profile_store = AsyncMock()
    profile_store.list_all.return_value = _profiles("p1")

    sup = Supervisor(planner, AsyncMock(), authorizer, limits, profile_store)

    events = await _collect(sup.supervise_stream("질문", _ctx(), _user_ctx()))

    assert [e["type"] for e in events] == ["token", "done"]
    assert events[-1]["data"]["streamed"] is True
    answer = events[-1]["data"]["response"].answer
    assert answer and events[0]["data"] == answer  # 빈 응답 금지 + 토큰=답변 일치


@pytest.mark.asyncio
async def test_stream_not_applied_when_replan_enabled():
    """replan이 켜져 있으면 단일 위임이라도 서브 토큰을 흘리지 않는다(결과가 뒤집힐 수 있음).

    replan 후에도 결과가 1건이면 passthrough(버퍼드)로 종료 — done.streamed=False,
    호출자가 답변을 단일 방출한다.
    """
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p1", subquery="q")]
    )
    planner.replan.return_value = DelegationPlan(delegations=[])

    runner = AsyncMock()
    sub_stream_called = {"count": 0}

    async def fake_run_stream(*args, **kwargs):
        sub_stream_called["count"] += 1
        yield {"type": "result", "data": SubAgentResult(profile="p1", answer="a", ok=True)}

    runner.run_stream = fake_run_stream

    async def fake_run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        return SubAgentResult(profile=profile_id, answer=f"{profile_id} 답변", ok=True)

    runner.run = AsyncMock(side_effect=fake_run)

    limits = SupervisorLimits(single_passthrough=True, adaptive_replan=True)
    sup = _make_supervisor(_profiles("p1"), planner, limits, runner=runner)

    events = await _collect(sup.supervise_stream("질문", _ctx(), _user_ctx()))

    assert sub_stream_called["count"] == 0  # 서브 스트리밍 미사용(run 사용)
    assert [e["type"] for e in events] == ["done"]
    assert events[0]["data"]["streamed"] is False
    assert events[0]["data"]["response"].answer == "p1 답변"
    planner.replan.assert_awaited_once()


@pytest.mark.asyncio
async def test_nonstream_supervise_unchanged_by_streaming_feature():
    """비스트리밍 supervise()는 emitter 없이 동작 — run_stream/synthesize_stream 미호출."""
    planner = AsyncMock()
    planner.decompose.return_value = DelegationPlan(
        delegations=[DelegationStep(profile="p1", subquery="q")]
    )
    planner.synthesize.return_value = "종합 답변"

    runner = AsyncMock()
    stream_called = {"count": 0}

    async def fake_run_stream(*args, **kwargs):
        stream_called["count"] += 1
        yield {"type": "result", "data": SubAgentResult(profile="p1", answer="a", ok=True)}

    runner.run_stream = fake_run_stream

    async def fake_run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        return SubAgentResult(profile=profile_id, answer=f"{profile_id} 답변", ok=True)

    runner.run = AsyncMock(side_effect=fake_run)

    limits = SupervisorLimits(single_passthrough=True)
    sup = _make_supervisor(_profiles("p1"), planner, limits, runner=runner)

    resp = await sup.supervise("질문", _ctx(), _user_ctx())

    assert stream_called["count"] == 0
    assert resp.answer == "p1 답변"  # passthrough(비스트리밍)


# --- SubAgentRunner.run_stream 계약 ---


@pytest.mark.asyncio
async def test_runner_run_stream_forwards_tokens_and_assembles_result():
    """run_stream은 execute_stream 토큰을 중계하고 마지막에 result 1건을 조립한다."""
    from src.domain.models import AgentMode

    profile_store = AsyncMock()
    profile_store.get.return_value = AgentProfile(id="p1", name="p1")

    ai_router = AsyncMock()
    ai_router.route.return_value = SimpleNamespace(mode=AgentMode.DETERMINISTIC)

    agent = AsyncMock()

    async def fake_execute_stream(*, question, plan, session_id, trace, context):
        yield {"type": "token", "data": "안녕"}
        yield {"type": "token", "data": "하세요"}
        yield {"type": "done", "data": {"sources": [{"document_id": "d1", "title": "t"}]}}

    agent.execute_stream = fake_execute_stream
    tool_registry = AsyncMock()
    tool_registry.resolve = lambda names: []

    runner = SubAgentRunner(
        profile_store=profile_store, ai_router=ai_router, agent=agent, tool_registry=tool_registry,
    )

    events = await _collect(runner.run_stream(
        "p1", "질문", AgentContext(session_id="s1"),
        user_security_level="PUBLIC", tenant_id="default",
    ))

    assert [e["type"] for e in events] == ["token", "token", "result"]
    result = events[-1]["data"]
    assert result.ok is True
    assert result.answer == "안녕하세요"
    assert result.sources == [{"document_id": "d1", "title": "t"}]


@pytest.mark.asyncio
async def test_runner_run_stream_blocks_workflow_same_as_run():
    """run_stream도 workflow_policy=block이면 실행 없이 실패 result만 낸다(가드 동일)."""
    from src.domain.models import AgentMode

    profile_store = AsyncMock()
    profile_store.get.return_value = AgentProfile(id="fortune-saju", name="사주")

    ai_router = AsyncMock()
    ai_router.route.return_value = SimpleNamespace(mode=AgentMode.WORKFLOW, workflow_id="saju")

    agent = AsyncMock()
    tool_registry = AsyncMock()
    tool_registry.resolve = lambda names: []

    runner = SubAgentRunner(
        profile_store=profile_store, ai_router=ai_router, agent=agent, tool_registry=tool_registry,
    )

    events = await _collect(runner.run_stream(
        "fortune-saju", "사주 봐줘", AgentContext(session_id="s1"),
        user_security_level="PUBLIC", tenant_id="default",
    ))

    assert [e["type"] for e in events] == ["result"]
    assert events[0]["data"].ok is False
    assert events[0]["data"].error == "workflow_delegation_unsupported"
    agent.execute.assert_not_called()
    agent.execute_stream.assert_not_called()
