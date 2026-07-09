"""Supervisor StateGraph 빌더 (P1-0).

P0의 명령형 async 루프(`supervisor.py`)를 LangGraph StateGraph로 재구성한다.
시스템 나머지(agent/workflow)가 전부 LangGraph인데 상위 Supervisor만 명령형이던
비일관을 해소하고, P1의 병렬 위임(`Send`)·adaptive replan(조건부 엣지)을 그래프
네이티브로 얹을 수 있는 토대를 만든다(설계문서 §7 Phase 1.5).

토폴로지:

    START → resolve_scope → detect_sticky
      --(route_by_sticky)--> sticky_delegate → END        (진행 중 워크플로우 연속)
                         └─> decompose
    decompose --(route_delegation)--> delegate | finalize
    delegate  --(route_delegation)--> delegate | finalize (순차 루프 = 조건부 self-edge)
    finalize → END

불변 계약(P0에서 그대로 승계):
- hub 강제(§0-5): delegate 노드는 오직 `plan.delegations`(메인의 계획)만 소비한다.
  서브 결과가 다음 위임 대상을 정하는 경로는 없다.
- 단일 관문(§0-3): `runner.run` 호출은 `is_delegation_allowed` 통과 블록 안에만 존재.
- 캡(P0-6): 위임 예산은 메인만 소유·소비하며 서브에 전달하지 않는다.
"""

from __future__ import annotations

import asyncio

from langgraph.graph import END, START, StateGraph

from src.config import settings
from src.domain.models import AgentResponse
from src.observability.logging import get_logger
from src.supervisor.authz import DelegationAuthorizer
from src.supervisor.limits import DelegationBudget
from src.supervisor.models import DelegationStep, SubAgentResult, SupervisorLimits
from src.supervisor.planner_llm import SupervisorPlanner
from src.supervisor.scoped_context import derive_scoped_context
from src.supervisor.state import SupervisorState
from src.supervisor.subagent_runner import SubAgentRunner

logger = get_logger(__name__)

# 그래프 슈퍼스텝 상한. 위임 캡(P0-6)과 별개인 최후 방어선 — decompose가 비정상적으로
# 긴 계획을 반환해도(denied 스텝은 예산을 소비하지 않아 캡만으로는 못 끊는다)
# 그래프가 무한히 돌지 않게 한다.
RECURSION_LIMIT = 50

STICKY_DELAY_NOTICE = (
    "진행 중인 상담 응답이 지연되고 있어요. 잠시 후 같은 대화에서 "
    "이어서 말씀해 주시면 계속 진행할게요."
)


def _create_run_delegation(runner: SubAgentRunner, limits: SupervisorLimits):
    """위임 1건 실행 헬퍼 — 타임아웃 상한 공용 적용(P0-6 확장).

    서브가 응답 없이 잡히면(외부 의존 행 등) supervise 전체가 무한 대기하며
    SSE가 ping만 보내는 사고를 차단한다. 워크플로우 핸드오프는 dynamic 스텝의
    로컬 LLM 생성이 오래 걸려(실측 50~120s+) 별도의 넉넉한 상한을 쓴다.
    """

    async def run_delegation(
        step: DelegationStep, sub_ctx, user_ctx, ctx, trace, workflow_policy: str
    ) -> SubAgentResult:
        timeout_sec = (
            limits.workflow_handoff_timeout_sec
            if workflow_policy == "handoff"
            else limits.delegation_timeout_sec
        )
        try:
            async with asyncio.timeout(timeout_sec):
                return await runner.run(
                    step.profile,
                    step.subquery,
                    sub_ctx,
                    user_security_level=user_ctx.security_level_max,
                    tenant_id=ctx.tenant_id,
                    trace=trace,
                    workflow_policy=workflow_policy,
                )
        except TimeoutError:
            logger.warning(
                "supervisor_delegation_timeout",
                profile=step.profile,
                timeout_sec=timeout_sec,
            )
            return SubAgentResult(
                profile=step.profile, answer="", ok=False, error="delegation_timeout",
            )

    return run_delegation


def _create_resolve_scope(authorizer: DelegationAuthorizer, profile_store):
    """allowed(deny-by-default, P0-4)와 위임 후보를 해석한다."""

    async def resolve_scope(state: SupervisorState) -> dict:
        supervisor_id = getattr(settings, "supervisor_profile_id", "supervisor")
        allowed = await authorizer.resolve_allowed(state["user_ctx"])
        all_profiles = await profile_store.list_all()
        candidates = [
            {"id": p.id, "name": p.name, "description": p.description}
            for p in all_profiles
            if p.id != supervisor_id and authorizer.is_delegation_allowed(allowed, p.id)
        ]
        return {
            "allowed": allowed,
            "all_profiles": all_profiles,
            "candidates": candidates,
            "supervisor_id": supervisor_id,
        }

    return resolve_scope


def _create_detect_sticky(authorizer: DelegationAuthorizer, workflow_engine):
    """진행 중인 서브 워크플로우 탐지 (sticky delegation).

    워크플로우 세션은 스코프 세션 id(`{parent}::sub::{profile}`)로 결정적으로
    파생되므로, 별도 상태 저장 없이 workflow engine이 곧 정본이다.
    결정권은 메인에 있다 — 서브가 요청하는 게 아니라 메인이 감지해 재위임한다(§0-5).
    """

    async def detect_sticky(state: SupervisorState) -> dict:
        if not workflow_engine:
            return {"sticky_profile": None}
        allowed = state["allowed"]
        for p in state["all_profiles"]:
            if p.id == state["supervisor_id"] or not authorizer.is_delegation_allowed(
                allowed, p.id
            ):
                continue
            scoped_id = f"{state['ctx'].session_id}::sub::{p.id}"
            try:
                session = await workflow_engine.get_session(scoped_id)
            except Exception:  # noqa: BLE001 - sticky 탐지 실패는 일반 경로로 degrade
                continue
            if session and not getattr(session, "completed", True):
                return {"sticky_profile": p.id}
        return {"sticky_profile": None}

    return detect_sticky


def route_by_sticky(state: SupervisorState) -> str:
    """sticky 감지 시 decompose를 우회한다 — 오라우팅 방지: decompose가 매 턴
    맥락 없이 재분류해 생년월일 답변 등이 엉뚱한 서브로 새는 문제를 차단."""
    return "sticky_delegate" if state["sticky_profile"] else "decompose"


def _create_sticky_delegate(runner: SubAgentRunner, limits: SupervisorLimits):
    """진행 중 워크플로우로 직행 재위임(멀티턴 연속성 — "위임 → 진행 → 복귀" 루프)."""

    run_delegation = _create_run_delegation(runner, limits)

    async def sticky_delegate(state: SupervisorState) -> dict:
        step = DelegationStep(
            profile=state["sticky_profile"],
            subquery=state["question"],
            reason="진행 중 워크플로우 연속(sticky)",
        )
        sub_ctx = derive_scoped_context(state["ctx"], step)
        r = await run_delegation(
            step, sub_ctx, state["user_ctx"], state["ctx"], state["trace"], "handoff"
        )
        logger.info(
            "supervisor_workflow_sticky",
            profile=state["sticky_profile"],
            ok=r.ok,
            error=r.error,
        )
        if r.ok:
            # 워크플로우 단계 응답은 그대로 전달(passthrough) — synthesize가
            # 단계 질문("생년월일을 알려주세요")을 훼손하면 안 된다.
            response = AgentResponse(answer=r.answer, sources=r.sources, trace=state["trace"])
        else:
            # sticky 실패 시 decompose로 폴백하지 않는다 — 워크플로우 중간 발화
            # ("투자" 등)를 단독 질문으로 재해석하면 무의미한 답이 나온다(실사고).
            # 워크플로우 세션은 보존되므로 재시도하면 sticky가 이어붙는다.
            response = AgentResponse(answer=STICKY_DELAY_NOTICE, sources=[], trace=state["trace"])
        return {"response": response}

    return sticky_delegate


def _create_decompose(planner: SupervisorPlanner, limits: SupervisorLimits):
    """질의 분해(P0-3) + 위임 루프 초기화(정책·예산·인덱스)."""

    async def decompose(state: SupervisorState) -> dict:
        plan = await planner.decompose(state["question"], state["allowed"], state["candidates"])
        # 단일 위임이면 워크플로우 핸드오프 허용(사주 등 인터랙티브 진입),
        # 다중 위임이면 차단(오라우팅된 워크플로우가 종합을 오염/지연시키는 것 방지).
        workflow_policy = "handoff" if len(plan.delegations) == 1 else "block"
        return {
            "plan": plan,
            "workflow_policy": workflow_policy,
            "budget": DelegationBudget(limits),  # (P0-6)
            "step_index": 0,
            "results": [],
        }

    return decompose


def route_delegation(state: SupervisorState) -> str:
    """순차 위임 루프의 조건부 엣지 — 남은 스텝과 예산을 모두 만족해야 계속."""
    plan = state["plan"]
    if state["step_index"] >= len(plan.delegations):
        return "finalize"
    if not state["budget"].can_delegate():
        # 캡 초과 → 안전 종료(부분 결과로 종합) (P0-6)
        logger.info("supervisor_delegation_cap_reached", remaining=state["budget"].remaining())
        return "finalize"
    return "delegate"


def _create_delegate(
    runner: SubAgentRunner, authorizer: DelegationAuthorizer, limits: SupervisorLimits
):
    """위임 1건 처리 — 관문 재검사 → scoped context 파생 → 실행 → 결과 수집.

    hub 강제(§0-5): 이 노드는 `plan.delegations[step_index]`만 소비한다.
    서브 결과(SubAgentResult)에는 재라우팅 필드가 계약상 없어, 서브가 다음
    위임 대상을 정할 방법이 없다. P0: adaptive replan 없음(P1-1 소유).
    """

    run_delegation = _create_run_delegation(runner, limits)

    async def delegate(state: SupervisorState) -> dict:
        step = state["plan"].delegations[state["step_index"]]
        next_index = state["step_index"] + 1

        if not authorizer.is_delegation_allowed(state["allowed"], step.profile):
            # 스코프 밖 위임 즉시 스킵 (§0-3, P0-4) — 예산은 소비하지 않는다.
            logger.warning("supervisor_delegation_denied", profile=step.profile)
            return {"step_index": next_index}

        sub_ctx = derive_scoped_context(state["ctx"], step)  # 최소권한 (P0-5)
        state["budget"].consume()

        # 단일 관문: runner.run 호출은 반드시 is_delegation_allowed 통과 블록 안에만
        # 존재한다(run_delegation이 타임아웃 상한과 함께 감싼다).
        r = await run_delegation(
            step, sub_ctx, state["user_ctx"], state["ctx"], state["trace"],
            state["workflow_policy"],
        )
        # §0-5: 위임 경로 전부 관측 — 성공/실패 위임을 운영자 트레이스로 남긴다.
        # (계층 트레이스 노드는 P1-3 소유. 지금은 구조화 로그로 최소 관측 보장.)
        logger.info(
            "supervisor_delegation_done",
            profile=step.profile,
            ok=r.ok,
            sources=len(r.sources),
            error=r.error,
        )
        # 서브는 메인에만 반환 (§0-5, P0-8). results는 새 리스트로 교체(불변성).
        return {"step_index": next_index, "results": [*state["results"], r]}

    return delegate


def _create_finalize(planner: SupervisorPlanner):
    """종료 판정 — 핸드오프 passthrough / 워크플로우 차단 안내 / synthesize 종합."""

    async def finalize(state: SupervisorState) -> dict:
        results = state["results"]
        trace = state["trace"]

        # 워크플로우 핸드오프(단일 위임): 단계 응답을 그대로 전달 — synthesize 금지.
        # 다음 턴은 detect_sticky가 이 워크플로우로 이어붙인다.
        if len(results) == 1 and results[0].ok and results[0].workflow_handoff:
            r0 = results[0]
            logger.info("supervisor_workflow_handoff_started", profile=r0.profile)
            return {
                "response": AgentResponse(answer=r0.answer, sources=r0.sources, trace=trace)
            }

        # 워크플로우 위임 차단으로 전부 실패한 경우: 일반 폴백 대신
        # "해당 챗봇을 직접 이용" 안내 — 사용자가 다음 행동을 알 수 있게(degrade UX).
        if results and not any(r.ok for r in results):
            wf_blocked = [r for r in results if r.error == "workflow_delegation_unsupported"]
            if wf_blocked:
                name_by_id = {p.id: p.name for p in state["all_profiles"]}
                names = ", ".join(f"'{name_by_id.get(r.profile, r.profile)}'" for r in wf_blocked)
                return {
                    "response": AgentResponse(
                        answer=(
                            f"요청하신 작업은 단계별 대화(정보 입력)가 필요해 통합 창구에서 "
                            f"바로 처리해 드리기 어렵습니다. {names} 챗봇을 직접 선택해 "
                            f"이용해 주세요."
                        ),
                        sources=[],
                        trace=trace,
                    )
                }

        answer = await planner.synthesize(state["question"], results)  # 메인이 종합·소유

        sources = []
        for r in results:
            if r.ok:
                sources.extend(r.sources)

        return {"response": AgentResponse(answer=answer, sources=sources, trace=trace)}

    return finalize


def build_supervisor_graph(
    planner: SupervisorPlanner,
    runner: SubAgentRunner,
    authorizer: DelegationAuthorizer,
    limits: SupervisorLimits,
    profile_store,
    workflow_engine=None,
):
    """Supervisor 위임 그래프를 빌드·컴파일한다.

    체크포인터는 아직 붙이지 않는다(단일 요청 in-memory). P1에서
    `AsyncPostgresSaver`를 연결할 때 상태 직렬화 경계를 함께 재설계한다.
    """
    workflow = StateGraph(SupervisorState)

    workflow.add_node("resolve_scope", _create_resolve_scope(authorizer, profile_store))
    workflow.add_node("detect_sticky", _create_detect_sticky(authorizer, workflow_engine))
    workflow.add_node("sticky_delegate", _create_sticky_delegate(runner, limits))
    workflow.add_node("decompose", _create_decompose(planner, limits))
    workflow.add_node("delegate", _create_delegate(runner, authorizer, limits))
    workflow.add_node("finalize", _create_finalize(planner))

    workflow.add_edge(START, "resolve_scope")
    workflow.add_edge("resolve_scope", "detect_sticky")
    workflow.add_conditional_edges(
        "detect_sticky",
        route_by_sticky,
        {"sticky_delegate": "sticky_delegate", "decompose": "decompose"},
    )
    workflow.add_edge("sticky_delegate", END)
    workflow.add_conditional_edges(
        "decompose", route_delegation, {"delegate": "delegate", "finalize": "finalize"}
    )
    # 순차 위임 루프: 조건부 self-edge. P1-2 병렬 위임은 이 자리를 Send fan-out으로 교체.
    workflow.add_conditional_edges(
        "delegate", route_delegation, {"delegate": "delegate", "finalize": "finalize"}
    )
    workflow.add_edge("finalize", END)

    return workflow.compile()
