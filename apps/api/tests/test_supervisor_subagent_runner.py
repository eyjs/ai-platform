"""SubAgentRunner 단위 테스트 (task-001, P0-1)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.agent_profile import AgentProfile
from src.domain.models import AgentResponse, TraceInfo
from src.supervisor.models import SubAgentResult
from src.supervisor.subagent_runner import SubAgentRunner


def _profile(profile_id: str = "insurance-qa") -> AgentProfile:
    return AgentProfile(id=profile_id, name="Insurance QA")


def _make_runner(profile=None, route_side_effect=None, execute_side_effect=None):
    """fake 컴포넌트로 SubAgentRunner를 구성한다."""
    profile_store = AsyncMock()
    profile_store.get.return_value = profile

    ai_router = AsyncMock()
    if route_side_effect is not None:
        ai_router.route.side_effect = route_side_effect
    else:
        ai_router.route.return_value = "fake_plan"

    agent = AsyncMock()
    if execute_side_effect is not None:
        agent.execute.side_effect = execute_side_effect
    else:
        agent.execute.return_value = AgentResponse(
            answer="서브 답변",
            sources=[],
            trace=TraceInfo(mode="agentic"),
        )

    tool_registry = AsyncMock()
    tool_registry.resolve = lambda tool_names: []

    runner = SubAgentRunner(
        profile_store=profile_store,
        ai_router=ai_router,
        agent=agent,
        tool_registry=tool_registry,
    )
    return runner, profile_store, ai_router, agent


@pytest.mark.asyncio
async def test_run_success_maps_agent_response_to_subagent_result():
    """정상 경로: route가 skip_context_resolve=True로 1회 호출되고 결과가 매핑된다."""
    profile = _profile("insurance-qa")
    runner, profile_store, ai_router, agent = _make_runner(profile=profile)
    ctx = AgentContext(session_id="s1", conversation_history=[{"role": "user", "content": "이전 대화"}])

    result = await runner.run(
        "insurance-qa",
        "실손보험 특약 알려줘",
        ctx,
        user_security_level="PUBLIC",
        tenant_id="tenant-a",
    )

    assert isinstance(result, SubAgentResult)
    assert result.ok is True
    assert result.profile == "insurance-qa"
    assert result.answer == "서브 답변"
    assert result.sources == []

    ai_router.route.assert_awaited_once()
    _, kwargs = ai_router.route.call_args
    assert kwargs["skip_context_resolve"] is True
    assert kwargs["query"] == "실손보험 특약 알려줘"
    assert kwargs["profile"] is profile
    assert kwargs["history"] == ctx.conversation_history
    assert kwargs["user_security_level"] == "PUBLIC"
    assert kwargs["tenant_id"] == "tenant-a"
    assert kwargs["session_scope_id"] is None
    assert kwargs["external_context"] == ""

    agent.execute.assert_awaited_once()
    _, exec_kwargs = agent.execute.call_args
    assert exec_kwargs["question"] == "실손보험 특약 알려줘"
    assert exec_kwargs["session_id"] == "s1"
    assert exec_kwargs["context"] is ctx


@pytest.mark.asyncio
async def test_run_profile_not_found_skips_agent_execute():
    """프로파일이 없으면 실패 결과를 반환하고 agent.execute는 호출되지 않는다."""
    runner, profile_store, ai_router, agent = _make_runner(profile=None)
    ctx = AgentContext(session_id="s1")

    result = await runner.run(
        "unknown-profile",
        "질문",
        ctx,
        user_security_level="PUBLIC",
        tenant_id="tenant-a",
    )

    assert result.ok is False
    assert result.error == "profile_not_found"
    assert result.profile == "unknown-profile"
    ai_router.route.assert_not_awaited()
    agent.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_agent_execute_exception_is_swallowed_and_reported():
    """agent.execute 예외는 전파되지 않고 ok=False, error로 매핑된다."""
    profile = _profile("insurance-qa")

    async def _boom(*args, **kwargs):
        raise RuntimeError("agent boom")

    runner, profile_store, ai_router, agent = _make_runner(
        profile=profile, execute_side_effect=_boom,
    )
    ctx = AgentContext(session_id="s1")

    result = await runner.run(
        "insurance-qa",
        "질문",
        ctx,
        user_security_level="PUBLIC",
        tenant_id="tenant-a",
    )

    assert result.ok is False
    assert result.error == "agent boom"
    assert result.profile == "insurance-qa"


def test_subagent_result_has_no_rerouting_fields():
    """계약 검증(P0-8): SubAgentResult에 재라우팅 필드가 존재하지 않는다."""
    result = SubAgentResult(profile="p", answer="a")

    for forbidden in ("next_profile", "route_to", "next_step", "delegate_to"):
        assert not hasattr(result, forbidden), f"금지된 재라우팅 필드가 존재함: {forbidden}"
