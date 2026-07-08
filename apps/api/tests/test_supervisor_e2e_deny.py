"""시나리오 C — deny-by-default e2e (supervisor-e2e-scenarios.md §C, requirement §7 DoD).

스코프 밖 위임이 실제 인가 관문(DelegationAuthorizer)에서 차단되고, 인가되지 않은
서브(SubAgentRunner.run)가 단 한 번도 호출되지 않으며, 크래시 없이 안전한 폴백
응답으로 종료됨을 라우트 레벨(엔트리 /chat)로 검증한다.

경계 설계:
  - `{fortune-saju}` 스코프 자격으로 `chatbot_id="supervisor"` 호출.
  - strict 모드(`profile_auth_strict=True`)는 `DelegationAuthorizer` 생성자 인자로
    직접 주입한다(conftest 전역 스위치를 건드리지 않고, 이 스위트 스코프에서만 strict 재현).
  - decompose만 fake로 대체해 "스코프 밖(insurance-qa) 위임 1건"을 하드코딩 반환한다.
    이렇게 하면 planner의 candidate 필터링(1차 방어선)을 우회해, Supervisor 루프
    내부의 `is_delegation_allowed` 런타임 재검사(2차 방어선, §0-3)를 독립적으로
    직접 검증할 수 있다 — 두 방어선이 모두 존재하는 실제 아키텍처에서, 이 테스트는
    "1차가 뚫려도 2차가 반드시 막는다"는 가장 강한 보장을 확인한다.
  - authz/limits/synthesize(폴백 경로)는 실제 프로덕션 코드 그대로 사용한다.

이 파일은 테스트 전용 추가다. 프로덕션 소스는 일절 수정하지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.agent_profile import AgentProfile
from src.gateway.models import ChatRequest, UserContext
from src.gateway.routes import chat as chat_module
from src.supervisor.authz import DelegationAuthorizer
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult, SupervisorLimits
from src.supervisor.planner_llm import FALLBACK_NO_RESULT, SupervisorPlanner
from src.supervisor.supervisor import Supervisor

SUPERVISOR_ID = "supervisor"


class _FakeProfileStore:
    """supervisor + insurance-qa(스코프 밖) + fortune-saju(스코프 내) 후보 저장소."""

    def __init__(self) -> None:
        self._profiles = {
            SUPERVISOR_ID: AgentProfile(id=SUPERVISOR_ID, name="Supervisor", description="메인 슈퍼바이저"),
            "insurance-qa": AgentProfile(id="insurance-qa", name="보험 QA", description="보험 상담"),
            "fortune-saju": AgentProfile(id="fortune-saju", name="사주 상담", description="사주팔자 상담"),
        }

    async def list_all(self) -> list[AgentProfile]:
        return list(self._profiles.values())

    async def get(self, profile_id: str) -> AgentProfile | None:
        return self._profiles.get(profile_id)


class _FakeTenantService:
    async def get_allowed_profiles(self, tenant_id):
        return []


class SpySubAgentRunner:
    """스코프 밖 서브가 실제로 호출되는지 증명하는 spy. 호출되면 즉시 기록한다."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(
        self,
        profile_id: str,
        query: str,
        ctx,
        *,
        user_security_level: str,
        tenant_id: str,
        trace=None,
    ) -> SubAgentResult:
        self.calls.append(profile_id)
        # 호출되어서는 안 되는 경로 — 호출되면 명시적으로 실패 결과를 반환(방어적).
        return SubAgentResult(profile=profile_id, answer="이 경로는 호출되면 안 됩니다", ok=True)


def _make_scoped_user_ctx() -> UserContext:
    """스코프 키 — allowed_profiles={fortune-saju}만 허용, 보험은 자격 밖."""
    return UserContext(
        user_id="scoped-1",
        user_role="VIEWER",
        security_level_max="PUBLIC",
        allowed_profiles=["fortune-saju"],
        tenant_id=None,
    )


def _make_request(state) -> MagicMock:
    request = MagicMock()
    request.app.state = state
    request.client = None
    return request


def _build_real_supervisor_with_fake_decompose(runner) -> tuple[Supervisor, DelegationAuthorizer]:
    """실제 Supervisor 루프 + 실제(strict) authz + fake decompose(스코프 밖 위임 하드코딩)."""
    profile_store = _FakeProfileStore()
    # strict=True를 이 테스트 스코프에서만 재현 — conftest 전역 스위치는 건드리지 않는다.
    authorizer = DelegationAuthorizer(
        profile_store=profile_store,
        tenant_service=_FakeTenantService(),
        access_policy=None,
        settings=SimpleNamespace(profile_auth_strict=True),
    )

    # synthesize는 실제 구현 그대로 사용(폴백 문구가 실제 정본과 일치함을 검증).
    # llm은 decompose를 fake로 덮어써 사용하지 않으므로 AsyncMock으로 충분.
    planner = SupervisorPlanner(orchestration_llm=AsyncMock())

    async def _fake_decompose(question, allowed, candidates):
        """candidate 필터링을 우회해 스코프 밖(insurance-qa) 위임 1건을 강제 생성한다."""
        return DelegationPlan(
            delegations=[DelegationStep(profile="insurance-qa", subquery=question, reason="스코프 밖 강제 위임")],
        )

    planner.decompose = _fake_decompose  # 인스턴스 메서드 오버라이드(테스트 전용, 소스 무편집)

    supervisor = Supervisor(
        planner=planner,
        runner=runner,
        authorizer=authorizer,
        limits=SupervisorLimits(),
        profile_store=profile_store,
    )
    return supervisor, authorizer


def _make_state(supervisor: Supervisor) -> SimpleNamespace:
    settings = SimpleNamespace(supervisor_profile_id=SUPERVISOR_ID, default_tenant_id="default")
    session_memory = AsyncMock()
    session_memory.get_turns.return_value = []
    return SimpleNamespace(settings=settings, supervisor=supervisor, session_memory=session_memory)


# ---------------------------------------------------------------------------
# 시나리오 C 본체
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_c_authorizer_denies_out_of_scope_profile_directly():
    """(C-1) 인가 관문 단위 검증: resolve_allowed가 스코프 집합을 산출하고
    is_delegation_allowed(insurance-qa)가 False다."""
    profile_store = _FakeProfileStore()
    authorizer = DelegationAuthorizer(
        profile_store=profile_store,
        tenant_service=_FakeTenantService(),
        access_policy=None,
        settings=SimpleNamespace(profile_auth_strict=True),
    )
    user_ctx = _make_scoped_user_ctx()

    allowed = await authorizer.resolve_allowed(user_ctx)

    assert allowed == {"fortune-saju"}
    assert authorizer.is_delegation_allowed(allowed, "insurance-qa") is False
    assert authorizer.is_delegation_allowed(allowed, "fortune-saju") is True


@pytest.mark.asyncio
async def test_scenario_c_out_of_scope_subagent_never_invoked(monkeypatch, caplog):
    """(C-2)★ 보안 핵심: 스코프 밖 위임(insurance-qa)이 decompose에서 만들어져도
    SubAgentRunner.run(insurance-qa, ...)가 실제로 0회 호출된다.
    (C-3) 인가된 서브가 없어 크래시/500 없이 안전한 폴백 응답으로 종료된다."""
    runner = SpySubAgentRunner()
    supervisor, authorizer = _build_real_supervisor_with_fake_decompose(runner)
    state = _make_state(supervisor)

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_scoped_user_ctx()))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    req = ChatRequest(question="보험 자기부담금 알려줘", chatbot_id=SUPERVISOR_ID)
    request = _make_request(state)

    with caplog.at_level("WARNING"):
        resp = await chat_module.chat(req, request)  # 크래시/500 없이 정상 반환되어야 한다.

    # (C-2)★ 스코프 밖 서브가 단 한 번도 실행되지 않았다 — 보안 핵심.
    assert runner.calls == []

    # 위임 스킵이 구조화 로그로 관측된다("supervisor_delegation_denied").
    denied_logs = [r for r in caplog.records if r.msg == "supervisor_delegation_denied"]
    assert len(denied_logs) == 1
    assert denied_logs[0]._structured_data.get("profile") == "insurance-qa"

    # (C-3) 인가된 서브가 없어 안전한 폴백 응답으로 종료(200 + 정본 폴백 문구).
    assert resp.answer == FALLBACK_NO_RESULT
    assert resp.sources == []

    # 이중 확인: 위 응답과 별개로 authorizer 자체도 동일 판정을 내린다(교차검증).
    allowed = await authorizer.resolve_allowed(_make_scoped_user_ctx())
    assert authorizer.is_delegation_allowed(allowed, "insurance-qa") is False


@pytest.mark.asyncio
async def test_scenario_c_full_chat_entrypoint_returns_200_not_500(monkeypatch):
    """전체 /chat 엔트리 통과 시 예외 전파 없이 200 상당(정상 반환)으로 안전 종료된다."""
    runner = SpySubAgentRunner()
    supervisor, _ = _build_real_supervisor_with_fake_decompose(runner)
    state = _make_state(supervisor)

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_scoped_user_ctx()))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    req = ChatRequest(question="보험 자기부담금 알려줘", chatbot_id=SUPERVISOR_ID)
    request = _make_request(state)

    # 예외 없이 반환되어야 한다(HTTPException 500 등 발생 금지).
    resp = await chat_module.chat(req, request)

    assert resp.response_id is not None
    assert isinstance(resp.answer, str) and resp.answer  # 빈 응답 금지(안전 폴백 문구 존재)
