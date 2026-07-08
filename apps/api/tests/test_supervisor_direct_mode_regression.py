"""직접 모드 회귀 없음 e2e (task-007, P0-7, requirement §0-2/DoD §7-1).

Supervisor 레이어(task-001~006)를 얹은 뒤에도 외부 서비스가 쓰는 **단일 chatbot_id 직접
호출** 시나리오가 100% 동일하게 동작함을 증명한다. 이 파일은 **테스트 전용 추가**이며
프로덕션 소스는 일절 수정하지 않는다(§0-2).

검증 시나리오:
  1. 분기 미진입 — chatbot_id="insurance-qa" 요청 시 state.supervisor.supervise가 호출되지
     않고 기존 _prepare_chat → state.agent.execute 경로가 그대로 실행된다.
  2. skip_context_resolve 보존 — 직접 모드에서 ai_router.route가 skip_context_resolve=True로
     호출된다(기존 계약, supervisor 도입이 이 인자를 바꾸지 않음).
  3. 오케스트레이터 경로 보존 — chatbot_id=None 요청이 여전히 state.orchestrator.route로
     흐른다(P2 흡수 전이므로 supervisor로 흡수되지 않음).
  4. 응답 형태 동일 — 직접 모드 응답이 AgentResponse(answer, sources, response_id) 형태.
  5. _is_supervisor_request가 supervisor가 아닌 임의 프로파일에서 False.
  6. 네거티브 — state.supervisor=None(미배선)에서도 직접 모드가 정상 동작(안전 폴백).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.agent_profile import AgentProfile
from src.domain.execution_plan import ExecutionPlan
from src.domain.models import AgentMode, AgentResponse, SearchScope
from src.gateway.models import ChatRequest, UserContext
from src.gateway.routes import chat as chat_module
from src.gateway.routes.helpers import _is_supervisor_request, _prepare_chat
from src.orchestrator.models import OrchestratorResult


def _make_user_ctx() -> UserContext:
    return UserContext(user_id="u1", user_role="VIEWER", security_level_max="PUBLIC", tenant_id=None)


def _make_profile(profile_id: str = "insurance-qa") -> AgentProfile:
    return AgentProfile(id=profile_id, name=profile_id, description=f"{profile_id} 프로파일")


def _make_full_state(
    *,
    supervisor=None,
    supervisor_profile_id: str = "supervisor",
    orchestrator=None,
    ai_router_return: ExecutionPlan | None = None,
) -> SimpleNamespace:
    """`_prepare_chat`/`chat()` 전체 경로가 요구하는 최소 실물 stub.

    직접 모드(`_prepare_chat`)가 실제로 실행되도록 ai_router/profile_store/
    session_memory/workflow_engine/tool_registry/auth_service 를 모두 채운다.
    """
    settings = SimpleNamespace(
        supervisor_profile_id=supervisor_profile_id,
        default_tenant_id="default",
        fallback_profile_id="general-chat",
    )

    session_memory = AsyncMock()
    session_memory.get_turns.return_value = []
    session_memory.get_orchestrator_metadata.return_value = {}

    profile_store = AsyncMock()
    profile_store.get.return_value = _make_profile()

    auth_service = AsyncMock()
    auth_service.check_profile_access.return_value = None

    workflow_engine = AsyncMock()
    workflow_engine.get_session.return_value = None  # 활성 워크플로우 없음

    tool_registry = MagicMock()
    tool_registry.resolve.return_value = []

    default_plan = ExecutionPlan(mode=AgentMode.AGENTIC, scope=SearchScope())
    ai_router = AsyncMock()
    ai_router.route.return_value = ai_router_return or default_plan

    agent = AsyncMock()
    agent.execute.return_value = AgentResponse(answer="직접 모드 답변", sources=[])

    return SimpleNamespace(
        settings=settings,
        supervisor=supervisor,
        orchestrator=orchestrator,
        session_memory=session_memory,
        profile_store=profile_store,
        auth_service=auth_service,
        workflow_engine=workflow_engine,
        tool_registry=tool_registry,
        ai_router=ai_router,
        agent=agent,
        request_log_service=None,
        response_cache_service=None,
    )


def _make_request(state) -> MagicMock:
    """`_get_app_state(request)`가 `request.app.state`를 그대로 반환하므로 이 형태로 stub."""
    request = MagicMock()
    request.app.state = state
    request.client = None
    return request


# ---------------------------------------------------------------------------
# 1. 분기 미진입 — chatbot_id 명시 시 supervise 미호출, 기존 경로 실행
# ---------------------------------------------------------------------------


class TestDirectModeBypassesSupervisor:
    """chatbot_id="insurance-qa" 직접 호출이 supervise를 우회한다."""

    @pytest.mark.asyncio
    async def test_direct_mode_never_touches_supervisor(self, monkeypatch):
        """직접 모드 전체 /chat 흐름에서 supervisor에는 어떤 호출도 가지 않는다(호출 spy 0건)."""
        supervisor = AsyncMock()
        state = _make_full_state(supervisor=supervisor)

        monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
        monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_user_ctx()))
        monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

        req = ChatRequest(question="보험 상품 알려줘", chatbot_id="insurance-qa")
        request = _make_request(state)

        resp = await chat_module.chat(req, request)

        # supervisor 쪽으로는 단 한 번의 호출도 발생하지 않는다("추가 호출 0" 증명).
        assert supervisor.mock_calls == []
        assert resp.answer == "직접 모드 답변"

        # 기존 경로(state.agent.execute)는 정확히 1회 실행된다.
        state.agent.execute.assert_awaited_once()
        assert state.agent.execute.await_args.kwargs["question"] == "보험 상품 알려줘"

    @pytest.mark.asyncio
    async def test_direct_mode_prepare_chat_used_not_supervise(self, monkeypatch):
        """`_prepare_chat`(실제 구현)가 그대로 실행되고 supervisor.supervise는 호출되지 않는다."""
        supervisor = AsyncMock()
        state = _make_full_state(supervisor=supervisor)

        monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
        monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_user_ctx()))
        monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

        req = ChatRequest(question="일반 질문", chatbot_id="insurance-qa")
        request = _make_request(state)

        await chat_module.chat(req, request)

        supervisor.supervise.assert_not_awaited()
        # _prepare_chat이 실제로 profile 조회/권한체크/ai_router.route까지 수행했음을 확인.
        state.profile_store.get.assert_awaited()
        state.auth_service.check_profile_access.assert_awaited()
        state.ai_router.route.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. skip_context_resolve 보존
# ---------------------------------------------------------------------------


class TestSkipContextResolvePreserved:
    """직접 모드(chatbot_id 명시)에서 ai_router.route가 skip_context_resolve=True로 호출된다."""

    @pytest.mark.asyncio
    async def test_prepare_chat_calls_ai_router_with_skip_context_resolve_true(self):
        state = _make_full_state()
        req = ChatRequest(question="질문", chatbot_id="insurance-qa")
        request = _make_request(state)
        user_ctx = _make_user_ctx()

        await _prepare_chat(req, request, user_ctx)

        state.ai_router.route.assert_awaited_once()
        _, kwargs = state.ai_router.route.call_args
        assert kwargs["skip_context_resolve"] is True

    @pytest.mark.asyncio
    async def test_full_chat_entrypoint_preserves_skip_context_resolve(self, monkeypatch):
        """엔트리(/chat)를 통째로 통과해도 동일 계약(skip_context_resolve=True)이 유지된다."""
        state = _make_full_state()
        monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
        monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_user_ctx()))
        monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

        req = ChatRequest(question="질문", chatbot_id="insurance-qa")
        request = _make_request(state)

        await chat_module.chat(req, request)

        _, kwargs = state.ai_router.route.call_args
        assert kwargs["skip_context_resolve"] is True


# ---------------------------------------------------------------------------
# 3. 오케스트레이터 경로 보존 (chatbot_id=None)
# ---------------------------------------------------------------------------


class TestOrchestratorPathPreserved:
    """chatbot_id=None 요청은 여전히 state.orchestrator.route로 흐른다(supervisor 미흡수)."""

    @pytest.mark.asyncio
    async def test_prepare_chat_routes_through_orchestrator_when_chatbot_id_none(self):
        orchestrator = AsyncMock()
        orchestrator.route.return_value = OrchestratorResult(
            selected_profile_id="insurance-qa",
            reason="test-route",
            is_general_response=False,
        )
        state = _make_full_state(orchestrator=orchestrator)
        req = ChatRequest(question="아무 질문")  # chatbot_id 생략
        request = _make_request(state)
        user_ctx = _make_user_ctx()

        setup = await _prepare_chat(req, request, user_ctx)

        orchestrator.route.assert_awaited_once()
        assert setup.orchestrated is True
        assert setup.profile_id == "insurance-qa"

    @pytest.mark.asyncio
    async def test_full_chat_entrypoint_orchestrator_path_never_touches_supervisor(self, monkeypatch):
        """chatbot_id=None 전체 /chat 흐름에서도 supervisor는 전혀 호출되지 않는다."""
        orchestrator = AsyncMock()
        orchestrator.route.return_value = OrchestratorResult(
            selected_profile_id="insurance-qa",
            reason="test-route",
            is_general_response=False,
        )
        supervisor = AsyncMock()
        state = _make_full_state(orchestrator=orchestrator, supervisor=supervisor)

        monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
        monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_user_ctx()))
        monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

        req = ChatRequest(question="아무 질문")  # chatbot_id 생략 -> 오케스트레이터 모드
        request = _make_request(state)

        resp = await chat_module.chat(req, request)

        orchestrator.route.assert_awaited_once()
        assert supervisor.mock_calls == []
        assert resp.answer == "직접 모드 답변"  # agent.execute 경로 재사용(오케스트레이터가 프로필만 선택)


# ---------------------------------------------------------------------------
# 4. 응답 형태 동일 (AgentResponse: answer/sources/response_id)
# ---------------------------------------------------------------------------


class TestResponseShapeUnchanged:
    """직접 모드 응답이 기존과 동일한 AgentResponse 형태를 유지한다."""

    @pytest.mark.asyncio
    async def test_direct_mode_response_is_agent_response_with_expected_fields(self, monkeypatch):
        state = _make_full_state()
        state.agent.execute.return_value = AgentResponse(
            answer="정확한 답변입니다",
            sources=[],
        )

        monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
        monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_user_ctx()))
        monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

        req = ChatRequest(question="질문", chatbot_id="insurance-qa")
        request = _make_request(state)

        resp = await chat_module.chat(req, request)

        assert isinstance(resp, AgentResponse)
        assert resp.answer == "정확한 답변입니다"
        assert resp.sources == []
        assert resp.response_id is not None  # api 레이어(엔트리)가 단일 출처로 주입(Task 014)
        # 내부 전달용 guardrail_score는 응답에서 제거된다(기존 계약).
        assert resp.guardrail_score is None


# ---------------------------------------------------------------------------
# 5. _is_supervisor_request가 supervisor가 아닌 임의 프로파일에서 False
# ---------------------------------------------------------------------------


class TestIsSupervisorRequestFalseForArbitraryProfile:
    """supervisor가 아닌 chatbot_id는 항상 False로 판별된다."""

    @pytest.mark.parametrize(
        "chatbot_id",
        ["insurance-qa", "kms-assistant", "fortune-saju", "general-chat", "unknown-profile"],
    )
    def test_arbitrary_profile_is_not_supervisor_request(self, chatbot_id):
        state = _make_full_state(supervisor=AsyncMock())
        assert _is_supervisor_request(chatbot_id, state) is False

    def test_only_configured_supervisor_id_is_true(self):
        state = _make_full_state(supervisor=AsyncMock(), supervisor_profile_id="supervisor")
        assert _is_supervisor_request("supervisor", state) is True
        assert _is_supervisor_request("insurance-qa", state) is False


# ---------------------------------------------------------------------------
# 6. 네거티브 — supervisor 미배선(state.supervisor=None)에서도 직접 모드 정상 동작
# ---------------------------------------------------------------------------


class TestSupervisorUnwiredSafeFallback:
    """state.supervisor=None(미배선) 환경에서도 직접 모드가 정상 동작한다(안전 폴백)."""

    @pytest.mark.asyncio
    async def test_direct_mode_works_when_supervisor_none(self, monkeypatch):
        state = _make_full_state(supervisor=None)

        monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
        monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_user_ctx()))
        monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

        req = ChatRequest(question="보험 문의", chatbot_id="insurance-qa")
        request = _make_request(state)

        resp = await chat_module.chat(req, request)

        assert resp.answer == "직접 모드 답변"
        state.agent.execute.assert_awaited_once()

    def test_is_supervisor_request_false_when_supervisor_none_even_with_matching_id(self):
        """chatbot_id가 supervisor_profile_id와 일치해도 state.supervisor=None이면 False."""
        state = _make_full_state(supervisor=None, supervisor_profile_id="supervisor")
        assert _is_supervisor_request("supervisor", state) is False


# ---------------------------------------------------------------------------
# 기준선 비교: supervisor 유무와 무관하게 직접 모드 라우팅 호출 시퀀스가 동일
# ---------------------------------------------------------------------------


class TestRoutingCallSequenceUnaffectedBySupervisor:
    """supervisor가 배선되어 있든 없든 직접 모드의 라우팅 호출 시퀀스(횟수)가 동일하다.

    "supervisor 도입 후 직접 모드에 추가 호출 0"을 시퀀스 비교로 증명한다.
    """

    @pytest.mark.asyncio
    async def test_call_counts_identical_with_and_without_supervisor(self, monkeypatch):
        async def _run_direct_mode(supervisor):
            state = _make_full_state(supervisor=supervisor)
            monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
            monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_user_ctx()))
            monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

            req = ChatRequest(question="동일 질문", chatbot_id="insurance-qa")
            request = _make_request(state)
            await chat_module.chat(req, request)
            return state

        state_without_supervisor = await _run_direct_mode(None)
        state_with_supervisor = await _run_direct_mode(AsyncMock())

        # 두 경우 모두 기존 경로 호출 횟수가 완전히 동일 — supervisor 존재 여부가
        # 직접 모드의 호출 시퀀스에 어떤 영향도 주지 않는다.
        assert (
            state_without_supervisor.ai_router.route.await_count
            == state_with_supervisor.ai_router.route.await_count
            == 1
        )
        assert (
            state_without_supervisor.agent.execute.await_count
            == state_with_supervisor.agent.execute.await_count
            == 1
        )


# ---------------------------------------------------------------------------
# 7. 실제 SubAgentRunner 스파이 0회 (P0 인수 시나리오 B, requirement §7 DoD)
# ---------------------------------------------------------------------------
#
# 위 1번 클래스는 state.supervisor 전체를 AsyncMock으로 감싸 "supervisor.mock_calls == []"
# 로 무호출을 증명한다. 이 클래스는 그와 별개로, 실제 Supervisor 인스턴스 + 실제
# SubAgentRunner 대신 주입한 spy를 사용해 "SubAgentRunner.run이 정확히 0회 호출됨"을
# supervisor-e2e-scenarios.md §B-1의 문구("SubAgentRunner/supervisor.* 코드 경로
# 미진입")대로 명시적으로 증명한다.


class _SpySubAgentRunner:
    """직접 모드에서 절대 호출되면 안 되는 서브 실행 경로의 spy."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, profile_id, query, ctx, *, user_security_level, tenant_id, trace=None):
        self.calls.append(profile_id)
        raise AssertionError("직접 모드에서 SubAgentRunner.run이 호출되어서는 안 된다")


class TestDirectModeSubAgentRunnerSpyZeroCalls:
    """직접 모드(chatbot_id="insurance-qa")에서 실제 SubAgentRunner.run이 0회 호출된다."""

    @pytest.mark.asyncio
    async def test_real_supervisor_with_spy_runner_never_invoked_in_direct_mode(self, monkeypatch):
        from src.supervisor.authz import DelegationAuthorizer
        from src.supervisor.models import SupervisorLimits
        from src.supervisor.planner_llm import SupervisorPlanner
        from src.supervisor.supervisor import Supervisor

        spy_runner = _SpySubAgentRunner()

        class _FakeProfileStore:
            async def list_all(self):
                return [_make_profile("supervisor"), _make_profile("insurance-qa")]

            async def get(self, profile_id):
                return _make_profile(profile_id)

        class _FakeTenantService:
            async def get_allowed_profiles(self, tenant_id):
                return []

        authorizer = DelegationAuthorizer(
            profile_store=_FakeProfileStore(),
            tenant_service=_FakeTenantService(),
            access_policy=None,
            settings=SimpleNamespace(profile_auth_strict=False),
        )
        supervisor = Supervisor(
            planner=SupervisorPlanner(orchestration_llm=AsyncMock()),
            runner=spy_runner,
            authorizer=authorizer,
            limits=SupervisorLimits(),
            profile_store=_FakeProfileStore(),
        )
        state = _make_full_state(supervisor=supervisor)  # chatbot_id != supervisor_profile_id

        monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
        monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_user_ctx()))
        monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

        req = ChatRequest(question="보험 상품 알려줘", chatbot_id="insurance-qa")
        request = _make_request(state)

        resp = await chat_module.chat(req, request)

        # 실제 SubAgentRunner(spy)가 정확히 0회 호출됨 — supervisor 코드 경로 완전 미진입.
        assert spy_runner.calls == []
        assert resp.answer == "직접 모드 답변"  # 기존 직접 모드 경로(state.agent.execute)만 실행됨
        state.agent.execute.assert_awaited_once()
