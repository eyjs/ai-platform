"""시나리오 A — 멀티도메인 위임·종합 e2e (supervisor-e2e-scenarios.md §A, requirement §7 DoD).

Supervisor 핵심 가치 검증: 한 질문이 두 도메인(보험/사내규정)에 걸쳐 있을 때
decompose가 2건 위임을 만들고, 각 서브가 인가 재검사를 통과해 독립 실행되며,
최종 답변에 두 도메인의 정보가 모두 종합되는지 라우트 레벨(엔트리 /chat)로 검증한다.

경계 설계(중요):
  - Supervisor 루프(decompose→authz→위임→synthesize)·DelegationAuthorizer·
    SupervisorLimits·SupervisorPlanner는 **실제 프로덕션 코드**를 그대로 사용한다.
  - LLM 경계(오케스트레이션 llm.generate_json/generate)만 결정적 fake로 대체한다.
  - 서브 실행 경계(SubAgentRunner)만 fake로 대체해 "서브 답변"을 결정적으로 고정한다
    (SubAgentRunner 내부의 ai_router/agent 그래프까지 도는 것은 이 스위트 범위 밖 —
    conftest가 강제하는 stub LLM/DB 제약과도 무관하게 결정적 e2e를 만들기 위함).
  - 이 fake 구성 자체가 "서브→서브 호출 불가"(hub 강제)를 구조적으로 보장한다:
    FakeSubAgentRunner는 planner/supervisor 핸들을 전혀 갖지 않으므로 재귀 위임이
    코드상 불가능하다.

이 파일은 테스트 전용 추가다. 프로덕션 소스는 일절 수정하지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.agent_profile import AgentProfile
from src.domain.models import SourceRef
from src.gateway.concurrency_gate import ConcurrencyGate
from src.gateway.models import ChatRequest, UserContext
from src.gateway.routes import chat as chat_module
from src.supervisor.authz import DelegationAuthorizer
from src.supervisor.models import SubAgentResult, SupervisorLimits
from src.supervisor.planner_llm import SupervisorPlanner
from src.supervisor.supervisor import Supervisor

SUPERVISOR_ID = "supervisor"


# ---------------------------------------------------------------------------
# 결정적 fake — LLM 경계
# ---------------------------------------------------------------------------


class FakeOrchestrationLLM:
    """decompose(generate_json)/synthesize(generate) 경계용 결정적 fake.

    - generate_json: 스펙 A의 2위임(JSON)을 그대로 반환(§A-1).
    - generate: 프롬프트를 그대로 반환(echo). synthesize가 두 서브 답변을 프롬프트에
      임베드하므로, echo 결과 자체가 "실제 synthesize가 두 답변을 조립했는지"를
      비순환적으로 증명한다(fake가 답을 조작하지 않고 실제 조립 결과를 통과시킴).
    """

    def __init__(self) -> None:
        self.generate_json_calls: list[tuple[str, str]] = []
        self.generate_calls: list[tuple[str, str]] = []

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        self.generate_json_calls.append((prompt, system))
        return {
            "delegations": [
                {"profile": "insurance-qa", "subquery": "자기부담금", "reason": "보험 도메인 질의"},
                {"profile": "kms-assistant", "subquery": "청구 절차", "reason": "사내 규정 질의"},
            ]
        }

    async def generate(self, prompt: str, system: str = "") -> str:
        self.generate_calls.append((prompt, system))
        return prompt  # echo — synthesize가 조립한 프롬프트를 그대로 통과시킨다.


# ---------------------------------------------------------------------------
# 결정적 fake — 서브 실행 경계(SubAgentRunner)
# ---------------------------------------------------------------------------


class FakeSubAgentRunner:
    """서브 실행 경계 fake. profile_id별 고정 답변을 반환하고 호출을 spy한다.

    planner/supervisor 핸들을 전혀 갖지 않으므로 서브→서브 재귀 위임이
    구조적으로 불가능하다(hub 강제, §0-5).
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._answers = {
            "insurance-qa": SubAgentResult(
                profile="insurance-qa",
                answer="자기부담금은 20%입니다.",
                sources=[SourceRef(document_id="insurance-doc-1", title="실손보험 약관")],
                ok=True,
            ),
            "kms-assistant": SubAgentResult(
                profile="kms-assistant",
                answer="청구 절차는 3단계입니다.",
                sources=[SourceRef(document_id="kms-doc-1", title="사내 청구 규정")],
                ok=True,
            ),
        }

    async def run(
        self,
        profile_id: str,
        query: str,
        ctx,
        *,
        user_security_level: str,
        tenant_id: str,
        trace=None,
        workflow_policy: str = "block",
    ) -> SubAgentResult:
        self.calls.append({"profile_id": profile_id, "query": query, "tenant_id": tenant_id})
        result = self._answers.get(profile_id)
        if result is None:
            return SubAgentResult(profile=profile_id, answer="", ok=False, error="unexpected_profile")
        return result


# ---------------------------------------------------------------------------
# 실제 컴포넌트 배선 헬퍼
# ---------------------------------------------------------------------------


class _FakeProfileStore:
    """supervisor + insurance-qa + kms-assistant 후보를 제공하는 fake 저장소."""

    def __init__(self) -> None:
        self._profiles = {
            SUPERVISOR_ID: AgentProfile(id=SUPERVISOR_ID, name="Supervisor", description="메인 슈퍼바이저"),
            "insurance-qa": AgentProfile(
                id="insurance-qa", name="보험 QA", description="실손보험 자기부담금 등 보험 상담"
            ),
            "kms-assistant": AgentProfile(
                id="kms-assistant", name="사내규정 어시스턴트", description="사내 규정/청구 절차 안내"
            ),
        }

    async def list_all(self) -> list[AgentProfile]:
        return list(self._profiles.values())

    async def get(self, profile_id: str) -> AgentProfile | None:
        return self._profiles.get(profile_id)


class _FakeTenantService:
    async def get_allowed_profiles(self, tenant_id):
        return []


def _make_admin_user_ctx() -> UserContext:
    """admin 자격 — allowed_profiles=["*"] → resolve_allowed=None(전체 허용)."""
    return UserContext(
        user_id="admin-1",
        user_role="ADMIN",
        security_level_max="SECRET",
        allowed_profiles=["*"],
        tenant_id=None,
    )


def _build_real_supervisor(runner: FakeSubAgentRunner, llm: FakeOrchestrationLLM) -> Supervisor:
    """실제 Supervisor 루프 + 실제 authz/limits/planner + fake LLM/러너로 배선."""
    profile_store = _FakeProfileStore()
    authorizer = DelegationAuthorizer(
        profile_store=profile_store,
        tenant_service=_FakeTenantService(),
        access_policy=None,
        settings=SimpleNamespace(profile_auth_strict=False),
    )
    planner = SupervisorPlanner(orchestration_llm=llm)
    return Supervisor(
        planner=planner,
        runner=runner,
        authorizer=authorizer,
        limits=SupervisorLimits(),
        profile_store=profile_store,
    )


def _make_state(supervisor: Supervisor) -> SimpleNamespace:
    """`_run_supervisor_chat`이 요구하는 최소 app.state (settings/session_memory/supervisor)."""
    settings = SimpleNamespace(supervisor_profile_id=SUPERVISOR_ID, default_tenant_id="default")

    session_memory = AsyncMock()
    session_memory.get_turns.return_value = []

    return SimpleNamespace(
        settings=settings, supervisor=supervisor, session_memory=session_memory,
        concurrency_gate=ConcurrencyGate(limit=100),
    )


def _make_request(state) -> MagicMock:
    request = MagicMock()
    request.app.state = state
    request.client = None
    return request


# ---------------------------------------------------------------------------
# 시나리오 A 본체
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_a_multidomain_delegation_and_synthesis(monkeypatch, caplog):
    """멀티도메인 질의 → 2위임(A-1) → 인가 재검사(A-2) → 각 서브 독립 실행(A-3)
    → 종합 답변에 양 도메인 정보 모두 포함(A-4, 핵심)."""
    llm = FakeOrchestrationLLM()
    runner = FakeSubAgentRunner()
    supervisor = _build_real_supervisor(runner, llm)
    state = _make_state(supervisor)

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=_make_admin_user_ctx()))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    req = ChatRequest(
        question="실손보험 자기부담금이 얼마인지랑, 사내 규정의 보험금 청구 절차도 알려줘",
        chatbot_id=SUPERVISOR_ID,
    )
    request = _make_request(state)

    with caplog.at_level("INFO"):
        resp = await chat_module.chat(req, request)

    # (A-1) decompose가 위임 2건을 만든다 — 1건만 나오면 실패.
    assert len(llm.generate_json_calls) == 1

    # (A-3) 두 서브 모두 run_subagent로 실행되고 각각 메인에 반환된다.
    called_profiles = [c["profile_id"] for c in runner.calls]
    assert set(called_profiles) == {"insurance-qa", "kms-assistant"}
    assert len(runner.calls) == 2  # 정확히 2회 — 중복/누락 없음

    # (A-2) 각 위임 직전 인가 재검사 통과가 구조화 로그로 관측된다(§0-5).
    # (src.observability.logging.StructuredLogger는 표준 logging.Logger.handle을 그대로
    # 사용하므로 caplog가 record.msg == 이벤트명을 그대로 캡처한다.)
    delegation_done_logs = [r for r in caplog.records if r.msg == "supervisor_delegation_done"]
    assert len(delegation_done_logs) == 2  # insurance-qa, kms-assistant 각 1회
    structured = [getattr(r, "_structured_data", {}) for r in delegation_done_logs]
    assert {d.get("profile") for d in structured} == {"insurance-qa", "kms-assistant"}
    assert all(d.get("ok") is True for d in structured)

    # (A-4)★ 최종 답변에 두 도메인의 정보가 모두 종합됨 — 핵심 어서션.
    assert "자기부담금" in resp.answer
    assert "청구 절차" in resp.answer

    # (A-5) 두 서브 결과가 synthesize에 전달됨 — echo된 프롬프트에 두 서브 답변이 임베드.
    assert "insurance-qa" in resp.answer
    assert "kms-assistant" in resp.answer
    assert len(llm.generate_calls) == 1  # synthesize는 1회만 호출(양 결과를 한 번에 종합)

    # 서브 결과의 출처(sources)가 메인 응답에 병합됨.
    source_doc_ids = {s.document_id for s in resp.sources}
    assert {"insurance-doc-1", "kms-doc-1"}.issubset(source_doc_ids)


@pytest.mark.asyncio
async def test_scenario_a_subagent_has_no_recursive_delegation_handle(monkeypatch):
    """구조적 hub 강제: FakeSubAgentRunner는 supervisor/planner 핸들을 전혀 갖지 않는다
    → 서브가 다른 서브를 재귀 위임하는 코드 경로 자체가 부재함을 증명(§0-5)."""
    runner = FakeSubAgentRunner()
    assert not hasattr(runner, "supervise")
    assert not hasattr(runner, "_planner")
    assert not hasattr(runner, "planner")
