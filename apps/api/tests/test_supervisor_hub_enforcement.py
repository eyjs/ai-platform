"""Hub 강제(서브→서브 경로 부재) 검증 (task-008, P0-8).

Supervisor 경로에서 서브가 다른 서브로 직접 위임/라우팅하는 코드 경로가
존재하지 않음(hub-and-spoke, §0-5, DoD §7-7)을 정적 검증 + 테스트로 증명한다.

이 테스트 파일은 소스를 수정하지 않는다 — 계약(부재)을 증명·회귀 가드할 뿐이다.
검증은 이미 구현된 인터페이스/루프(task-001/003)를 관찰한다:

1. 반환 계약(구조적 부재): SubAgentResult에 재라우팅 필드 없음.
2. 서브에 위임 능력 미주입: SubAgentRunner가 Supervisor/Planner/run_subagent 핸들 없음.
3. 루프가 메인 계획만 순회: 서브 결과가 임의값이어도 재위임을 만들지 않음(non-adaptive).
4. 정적 소스 스캔: subagent_runner.py/models.py에 서브→서브 경로 문자열 부재.
5. peer mesh 부재(행위): allowed 밖 위임은 decompose 산출물이어도 관문에서 차단.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.agent_profile import AgentProfile
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult, SupervisorLimits
from src.supervisor.subagent_runner import SubAgentRunner
from src.supervisor.supervisor import Supervisor

# 서브가 "다음 위임 대상"을 표현할 수 있다면 생길 법한 필드명 목록.
FORBIDDEN_REROUTE_FIELDS = (
    "next_profile",
    "route_to",
    "delegate_to",
    "handoff",
    "next_step",
    "next_delegation",
    "reroute",
)

# src/supervisor/ 디렉토리 경로를 하드코딩하지 않고 이 테스트 파일 기준 상대 계산.
_SUPERVISOR_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "supervisor"


class _FakeUserCtx:
    """resolve_allowed/is_delegation_allowed가 요구하는 최소 사용자 컨텍스트."""

    def __init__(self, security_level_max: str = "PUBLIC"):
        self.security_level_max = security_level_max


def _profiles(*ids: str) -> list[AgentProfile]:
    return [AgentProfile(id=pid, name=pid, description=f"{pid} 설명") for pid in ids]


def _make_authorizer(allowed):
    authorizer = AsyncMock()
    authorizer.resolve_allowed.return_value = allowed

    def _is_allowed(allowed_set, profile_id):
        if allowed_set is None:
            return True
        return profile_id in allowed_set

    authorizer.is_delegation_allowed = _is_allowed
    return authorizer


def _make_profile_store(profiles: list[AgentProfile]):
    store = AsyncMock()
    store.list_all.return_value = profiles
    return store


def _make_planner(plan: DelegationPlan, synthesize_answer: str = "종합 답변"):
    planner = AsyncMock()
    planner.decompose.return_value = plan
    planner.synthesize.return_value = synthesize_answer
    return planner


# ---------------------------------------------------------------------------
# 1. 반환 계약(구조적 부재): SubAgentResult에 재라우팅 필드가 없다.
# ---------------------------------------------------------------------------


def test_subagent_result_has_no_rerouting_fields():
    """서브가 '다음에 어디로 갈지'를 표현할 구조적 수단이 없어야 한다."""
    result = SubAgentResult(profile="p", answer="a")

    for forbidden in FORBIDDEN_REROUTE_FIELDS:
        assert not hasattr(result, forbidden), f"SubAgentResult에 금지된 재라우팅 필드 존재: {forbidden}"


def test_subagent_result_fields_are_exactly_declared_contract():
    """SubAgentResult의 필드 집합이 계약에서 벗어나지 않는다.

    workflow_handoff는 재라우팅 필드가 아니다 — "이 답변을 synthesize 없이 그대로
    전달하라"는 표식일 뿐, 다음 턴 수신자는 여전히 메인이 sticky 감지로 결정한다(§0-5).
    """
    field_names = {f.name for f in SubAgentResult.__dataclass_fields__.values()}
    # review_passed/review_note(P1-4)는 메인의 판정 기록이지 재라우팅 필드가 아니다 —
    # 값을 쓰는 주체도 메인(finalize 검토 게이트)이고, 다음 위임 대상을 정하지 않는다.
    # faithfulness_score는 서브 가드레일 점수의 관측 필드 — 값을 쓰는 주체는
    # 메인(요청 로그 영속)이고 다음 위임 대상을 정하지 않는다(재라우팅 아님).
    assert field_names == {
        "profile", "answer", "sources", "trace", "ok", "error", "workflow_handoff",
        "review_passed", "review_note", "faithfulness_score",
    }


# ---------------------------------------------------------------------------
# 2. 서브에 위임 능력 미주입: SubAgentRunner가 Supervisor/Planner 핸들을 갖지 않는다.
# ---------------------------------------------------------------------------


def test_subagent_runner_constructor_has_no_supervisor_or_planner_dependency():
    """SubAgentRunner 생성자 시그니처에 supervisor/planner 계열 파라미터가 없어야 한다."""
    sig = inspect.signature(SubAgentRunner.__init__)
    param_names = {name.lower() for name in sig.parameters if name != "self"}

    for forbidden_token in ("supervisor", "planner"):
        assert not any(forbidden_token in name for name in param_names), (
            f"SubAgentRunner 생성자에 금지된 파라미터 발견(토큰={forbidden_token}): {param_names}"
        )

    # 실제 계약: profile_store/ai_router/agent/tool_registry 4종만 주입된다.
    assert param_names == {"profile_store", "ai_router", "agent", "tool_registry"}


def test_subagent_runner_instance_attrs_have_no_supervisor_or_planner_handle():
    """SubAgentRunner 인스턴스 속성에도 supervisor/planner 참조가 없어야 한다."""
    profile_store = AsyncMock()
    ai_router = AsyncMock()
    agent = AsyncMock()
    tool_registry = AsyncMock()

    runner = SubAgentRunner(
        profile_store=profile_store,
        ai_router=ai_router,
        agent=agent,
        tool_registry=tool_registry,
    )

    attr_names = {name.lower() for name in vars(runner)}
    for forbidden_token in ("supervisor", "planner"):
        assert not any(forbidden_token in name for name in attr_names), (
            f"SubAgentRunner 인스턴스에 금지된 속성 발견(토큰={forbidden_token}): {attr_names}"
        )

    # run 메서드 자체도 다른 run_subagent/supervise를 재귀 호출할 매개변수를 갖지 않는다.
    run_sig = inspect.signature(runner.run)
    run_param_names = {name.lower() for name in run_sig.parameters}
    for forbidden_token in ("supervisor", "planner", "runner"):
        assert not any(forbidden_token in name for name in run_param_names), (
            f"SubAgentRunner.run 시그니처에 금지된 파라미터 발견(토큰={forbidden_token}): {run_param_names}"
        )


# ---------------------------------------------------------------------------
# 3. 루프가 메인 계획만 순회: 서브가 임의 결과를 반환해도 재위임을 만들지 않는다.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervise_loop_never_derives_new_delegation_from_subagent_result():
    """fake runner가 매번 다른(임의의) SubAgentResult를 반환해도 실행 위임 수는
    오직 plan.delegations의 길이로만 결정된다(P0 non-adaptive, DoD §7-7)."""
    profiles = _profiles("p1", "p2", "p3")
    plan = DelegationPlan(
        delegations=[
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
            DelegationStep(profile="p3", subquery="q3"),
        ],
        is_adaptive=False,
    )
    planner = _make_planner(plan)
    authorizer = _make_authorizer(None)
    limits = SupervisorLimits(max_delegations=10, max_depth=1)
    profile_store = _make_profile_store(profiles)

    call_log: list[str] = []

    async def _run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        call_log.append(profile_id)
        # 서브가 마치 다른 프로파일로 재라우팅을 유도하려는 듯한 "이상 결과"를 반환해도
        # SubAgentResult 계약에는 이를 담을 필드가 없다(테스트1과 상호검증) — 여기서는
        # 임의의 profile/answer/ok 조합만 반환 가능함을 보인다.
        return SubAgentResult(profile=f"attacker-injected-{profile_id}", answer="아무 값", ok=True)

    runner = AsyncMock()
    runner.run = AsyncMock(side_effect=_run)

    supervisor = Supervisor(planner, runner, authorizer, limits, profile_store)
    ctx = AgentContext(session_id="s1")
    user_ctx = _FakeUserCtx()

    await supervisor.supervise("질문", ctx, user_ctx)

    # plan.delegations가 3건이므로 정확히 3회만 호출된다 — 서브가 반환한
    # "attacker-injected-*" profile 값은 루프의 다음 위임 대상 산정에 전혀 쓰이지 않는다.
    assert call_log == ["p1", "p2", "p3"]
    assert runner.run.await_count == 3

    # decompose는 정확히 1회만 호출된다 — 서브 결과를 근거로 재decompose(replan)하지 않는다.
    planner.decompose.assert_awaited_once()
    assert plan.is_adaptive is False


@pytest.mark.asyncio
async def test_supervise_loop_does_not_call_decompose_again_mid_loop():
    """루프 도중 서브가 실패(ok=False)해도 decompose를 재호출하지 않는다(replan 부재)."""
    profiles = _profiles("p1", "p2")
    plan = DelegationPlan(
        delegations=[
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="p2", subquery="q2"),
        ]
    )
    planner = _make_planner(plan)
    authorizer = _make_authorizer(None)
    limits = SupervisorLimits(max_delegations=10, max_depth=1)
    profile_store = _make_profile_store(profiles)

    runner = AsyncMock()
    runner.run = AsyncMock(
        side_effect=[
            SubAgentResult(profile="p1", answer="", ok=False, error="boom"),
            SubAgentResult(profile="p2", answer="ok", ok=True),
        ]
    )

    supervisor = Supervisor(planner, runner, authorizer, limits, profile_store)
    ctx = AgentContext(session_id="s1")
    user_ctx = _FakeUserCtx()

    await supervisor.supervise("질문", ctx, user_ctx)

    planner.decompose.assert_awaited_once()
    assert runner.run.await_count == 2


# ---------------------------------------------------------------------------
# 4. 정적 소스 스캔: 서브→서브 경로를 만들 수 있는 문자열이 소스에 없다.
# ---------------------------------------------------------------------------


def test_static_scan_subagent_runner_has_no_peer_delegation_code():
    """subagent_runner.py 소스에 supervise/Supervisor(/SupervisorPlanner/재귀 run_subagent가 없다."""
    runner_path = _SUPERVISOR_SRC_DIR / "subagent_runner.py"
    assert runner_path.is_file(), f"경로 계산 실패 — 파일 없음: {runner_path}"
    source = runner_path.read_text(encoding="utf-8")

    forbidden_tokens = (
        "supervise(",
        "Supervisor(",
        "SupervisorPlanner",
        "run_subagent(",
        ".supervise(",
    )
    for token in forbidden_tokens:
        assert token not in source, f"subagent_runner.py에 금지된 서브→서브 경로 문자열 발견: {token}"


def test_static_scan_models_subagent_result_has_no_reroute_field_names():
    """models.py의 SubAgentResult 정의 블록에 next/route 계열 필드명이 없다."""
    models_path = _SUPERVISOR_SRC_DIR / "models.py"
    assert models_path.is_file(), f"경로 계산 실패 — 파일 없음: {models_path}"
    source = models_path.read_text(encoding="utf-8")

    # SubAgentResult 클래스 정의부만 추출(다음 최상위 @dataclass 전까지)해서 스캔 범위를
    # 좁힌다 — 파일 전체 스캔보다 오탐(다른 클래스의 정상 필드) 가능성을 낮춘다.
    marker = "class SubAgentResult"
    start = source.index(marker)
    rest = source[start:]
    # 다음 top-level 데코레이터(@dataclass) 등장 지점까지가 이 클래스 블록.
    next_marker_idx = rest.find("\n@dataclass", 1)
    block = rest if next_marker_idx == -1 else rest[:next_marker_idx]

    # 실제 필드 선언(예: `next_profile: str` 또는 `next_profile =`)만 위반으로 판단한다.
    # 클래스 docstring 안의 설명 문구(예: "next_profile/route_to 등")는 필드 선언이
    # 아니므로 오탐이 아니다 — 라인 시작에 필드명이 오고 뒤에 `:`가 오는지로 좁혀서 스캔한다.
    import re

    for forbidden in FORBIDDEN_REROUTE_FIELDS:
        pattern = re.compile(rf"^\s*{re.escape(forbidden)}\s*:", re.MULTILINE)
        assert not pattern.search(block), f"SubAgentResult 정의 블록에 금지된 필드 선언 발견: {forbidden}"


def test_static_scan_delegate_node_only_reads_plan_delegations():
    """graph.py의 위임 fan-out이 소비하는 대상이 'plan.delegations' 하나뿐임을 소스에서 확인한다.

    P1-2 병렬 전환으로 위임 Send는 dispatch 라우터가 메인의 계획을 순회하며 생성한다.
    서브 결과 리스트(results)에서 step/profile을 뽑아 재위임(Send)하는 패턴이
    없는지도 함께 본다.
    """
    graph_path = _SUPERVISOR_SRC_DIR / "graph.py"
    assert graph_path.is_file(), f"경로 계산 실패 — 파일 없음: {graph_path}"
    source = graph_path.read_text(encoding="utf-8")

    assert 'for step in state["plan"].delegations' in source, (
        "graph.py dispatch가 plan.delegations를 순회하는 코드를 찾지 못함 — 구조 변경 시 이 테스트를 갱신할 것"
    )
    # Send 생성 지점은 dispatch 라우터 하나여야 한다(위임 fan-out 단일 지점 = 단일 관문).
    assert source.count("Send(") == 1, (
        "delegate Send 생성 지점이 dispatch 라우터 밖에도 존재 — 관문 우회 위임 경로 의심"
    )

    # 서브 결과 리스트(results)를 순회하는 코드가 있다면(현재는 sources 수집용),
    # 그 블록 안에서 재위임을 시도하는 코드(runner 재호출/DelegationStep 생성)가
    # 없는지 확인한다 — 단순 'for r in results' 존재 자체는 위반이 아니다(sources 집계는 허용).
    import re

    for match in re.finditer(r"for \w+ in results:\n((?:[ \t]+.+\n)*)", source):
        loop_body = match.group(1)
        for forbidden_call in ("runner.run(", "run_delegation(", "DelegationStep(", "planner.decompose"):
            assert forbidden_call not in loop_body, (
                f"graph.py의 results 순회 블록에서 재위임 의심 코드 발견: {forbidden_call}"
            )

    # supervisor.py는 파사드로 축소되었다 — 위임 실행 코드가 남아있지 않아야 한다
    # (그래프 밖 우회 경로 부재).
    supervisor_path = _SUPERVISOR_SRC_DIR / "supervisor.py"
    facade_source = supervisor_path.read_text(encoding="utf-8")
    for forbidden_call in ("runner.run(", "self._runner.run", "decompose(", "synthesize("):
        assert forbidden_call not in facade_source, (
            f"supervisor.py(파사드)에 그래프 밖 위임 실행 의심 코드 발견: {forbidden_call}"
        )


# ---------------------------------------------------------------------------
# 5. peer mesh 부재(행위): allowed 밖 프로파일은 decompose 산출물이어도 관문에서 차단.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decompose_output_bypassing_allowed_scope_is_blocked_at_gate():
    """decompose가 (오염되었다고 가정하고) allowed 밖 프로파일을 위임 계획에 섞어 넣어도,
    루프의 단일 관문(is_delegation_allowed)에서 실행 전 차단된다 — task-004와 상호검증.

    이는 서브가 아니라 '메인의 계획'이 오염된 경우조차 실행 관문이 최종 방어선임을
    보여 peer mesh(서브가 관문 밖에서 우회 진입)가 구조적으로 불가능함을 뒷받침한다.
    """
    profiles = _profiles("insurance-qa")
    plan = DelegationPlan(
        delegations=[
            DelegationStep(profile="insurance-qa", subquery="허용된 질의"),
            DelegationStep(profile="peer-injected-profile", subquery="우회 시도"),
        ]
    )
    planner = _make_planner(plan)
    authorizer = _make_authorizer({"insurance-qa"})  # peer-injected-profile은 allowed 밖
    limits = SupervisorLimits(max_delegations=10, max_depth=1)
    profile_store = _make_profile_store(profiles)

    runner = AsyncMock()
    runner.run = AsyncMock(
        return_value=SubAgentResult(profile="insurance-qa", answer="답변", ok=True)
    )

    supervisor = Supervisor(planner, runner, authorizer, limits, profile_store)
    ctx = AgentContext(session_id="s1")
    user_ctx = _FakeUserCtx()

    await supervisor.supervise("질문", ctx, user_ctx)

    # allowed 안(insurance-qa)만 실행되고, peer-injected-profile은 관문에서 걸러진다.
    assert runner.run.await_count == 1
    assert runner.run.await_args.args[0] == "insurance-qa"


@pytest.mark.asyncio
async def test_every_executed_delegation_passed_through_allow_gate():
    """루프가 실제로 실행한 모든 위임이 authorizer.is_delegation_allowed를 통과했는지
    독립적으로 재검증한다(관문 우회 경로 부재의 행위적 증거)."""
    profiles = _profiles("p1", "p2", "p3")
    plan = DelegationPlan(
        delegations=[
            DelegationStep(profile="p1", subquery="q1"),
            DelegationStep(profile="blocked", subquery="q2"),
            DelegationStep(profile="p3", subquery="q3"),
        ]
    )
    planner = _make_planner(plan)
    allowed = {"p1", "p3"}
    authorizer = _make_authorizer(allowed)
    limits = SupervisorLimits(max_delegations=10, max_depth=1)
    profile_store = _make_profile_store(profiles)

    runner = AsyncMock()
    executed_profiles: list[str] = []

    async def _run(profile_id, subquery, ctx, *, user_security_level, tenant_id, trace=None, workflow_policy="block"):
        executed_profiles.append(profile_id)
        return SubAgentResult(profile=profile_id, answer="ok", ok=True)

    runner.run = AsyncMock(side_effect=_run)

    supervisor = Supervisor(planner, runner, authorizer, limits, profile_store)
    ctx = AgentContext(session_id="s1")
    user_ctx = _FakeUserCtx()

    await supervisor.supervise("질문", ctx, user_ctx)

    assert executed_profiles == ["p1", "p3"]
    for p in executed_profiles:
        assert authorizer.is_delegation_allowed(allowed, p) is True
    assert authorizer.is_delegation_allowed(allowed, "blocked") is False
