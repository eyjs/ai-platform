"""Supervisor StateGraph 빌더 (P1-0 전환 + P1-1~P1-4 확장).

P0의 명령형 async 루프를 LangGraph StateGraph로 재구성하고(P1-0), 그 위에
그래프 네이티브 기능을 얹는다:
- P1-1 adaptive replan: 라운드 완료 후 메인이 추가 위임을 판단(조건부 엣지). opt-in.
- P1-2 병렬 위임: 한 라운드의 위임들을 `Send` fan-out으로 동시 실행.
- P1-3 위임 트레이스: 위임 경로 전부를 `delegation_log`로 수집해 `TraceInfo`로 노출.
- P1-4 메인 검토 게이트: 서브 답변 판정(pass/fail) 후 통과분만 종합. opt-in.

토폴로지:

    START → resolve_scope → detect_sticky
      --(route_by_sticky)--> sticky_delegate → END           (진행 중 워크플로우 연속)
                         └─> decompose
    decompose --(route_dispatch)--> [Send delegate ×N] | finalize
    delegate  → collect                                       (fan-in: 라운드 배리어)
    collect   --(route_after_round)--> replan | finalize      (P1-1, opt-in)
    replan    --(route_dispatch)--> [Send delegate ×N] | finalize
    finalize  → END

불변 계약(P0에서 그대로 승계):
- hub 강제(§0-5): 위임 대상은 오직 메인의 계획(`plan.delegations`)에서만 나온다.
  서브 결과가 다음 위임 대상을 정하는 경로는 없다(replan도 메인 LLM의 결정).
- 단일 관문(§0-3): 모든 Send는 `is_delegation_allowed` 통과 후에만 생성된다.
- 캡(P0-6): 위임 예산은 메인만 소유·소비한다. replan 라운드에도 총 위임 수 상한 공유.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.config import settings
from src.domain.models import AgentResponse, TraceInfo
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
# 긴 계획을 반환해도 그래프가 무한히 돌지 않게 한다.
RECURSION_LIMIT = 50

STICKY_DELAY_NOTICE = (
    "진행 중인 상담 응답이 지연되고 있어요. 잠시 후 같은 대화에서 "
    "이어서 말씀해 주시면 계속 진행할게요."
)


def _delegation_trace_entry(
    step: DelegationStep, result: SubAgentResult, latency_ms: float, round_no: int
) -> dict:
    """위임 1건의 관측 레코드 (P1-3). 운영자 트레이스 패널이 그대로 표시한다."""
    return {
        "profile": step.profile,
        "subquery": step.subquery,
        "reason": step.reason,
        "ok": result.ok,
        "error": result.error,
        "workflow_handoff": result.workflow_handoff,
        "latency_ms": round(latency_ms, 1),
        "round": round_no,
    }


def _build_trace_info(state: SupervisorState, sticky: str | None = None) -> TraceInfo:
    """위임 경로 전체를 담은 TraceInfo (P1-3) — 위임은 사용자에겐 invisible,
    운영자에겐 transparent(§0-5)."""
    return TraceInfo(
        mode="supervisor",
        router_decision={
            "delegations": state["delegation_log"],
            "rounds": state["round"],
            "sticky": sticky,
        },
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
        # decompose 판단 신호: description 에 더해 domain_scopes·intent_hints 를 노출한다.
        # (실사고: description 부재 + 신호 미노출로 8B decompose 가 id/name 만 보고
        # 오라우팅. 프로필의 강한 신호를 라우팅 판단에 재사용 — 레거시 라우터가 쓰던 신호.)
        candidates = [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "domains": list(p.domain_scopes),
                "intents": [h.name for h in p.intent_hints],
            }
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
        started = time.monotonic()
        r = await run_delegation(
            step, sub_ctx, state["user_ctx"], state["ctx"], state["trace"], "handoff"
        )
        latency_ms = (time.monotonic() - started) * 1000
        logger.info(
            "supervisor_workflow_sticky",
            profile=state["sticky_profile"],
            ok=r.ok,
            error=r.error,
        )
        log_entry = _delegation_trace_entry(step, r, latency_ms, state["round"])
        trace_info = TraceInfo(
            mode="supervisor",
            router_decision={
                "delegations": [log_entry],
                "rounds": state["round"],
                "sticky": state["sticky_profile"],
            },
        )
        if r.ok:
            # 워크플로우 단계 응답은 그대로 전달(passthrough) — synthesize가
            # 단계 질문("생년월일을 알려주세요")을 훼손하면 안 된다.
            response = AgentResponse(answer=r.answer, sources=r.sources, trace=trace_info)
        else:
            # sticky 실패 시 decompose로 폴백하지 않는다 — 워크플로우 중간 발화
            # ("투자" 등)를 단독 질문으로 재해석하면 무의미한 답이 나온다(실사고).
            # 워크플로우 세션은 보존되므로 재시도하면 sticky가 이어붙는다.
            response = AgentResponse(answer=STICKY_DELAY_NOTICE, sources=[], trace=trace_info)
        return {"response": response, "delegation_log": [log_entry]}

    return sticky_delegate


def _create_decompose(planner: SupervisorPlanner, limits: SupervisorLimits):
    """질의 분해(P0-3) + 위임 루프 초기화(정책·예산)."""

    async def decompose(state: SupervisorState) -> dict:
        plan = await planner.decompose(state["question"], state["allowed"], state["candidates"])
        # 단일 위임이면 워크플로우 핸드오프 허용(사주 등 인터랙티브 진입),
        # 다중 위임이면 차단(오라우팅된 워크플로우가 종합을 오염/지연시키는 것 방지).
        workflow_policy = "handoff" if len(plan.delegations) == 1 else "block"
        return {
            "plan": plan,
            "workflow_policy": workflow_policy,
            "budget": DelegationBudget(limits),  # (P0-6)
            "round": 0,
        }

    return decompose


def _create_route_dispatch(authorizer: DelegationAuthorizer, limits: SupervisorLimits):
    """위임 fan-out 라우터 (P1-2) — 이번 라운드 계획을 병렬 Send 태스크로 변환한다.

    단일 관문(§0-3): Send는 `is_delegation_allowed` 통과 후에만 생성되므로,
    delegate 노드에 도달하는 모든 위임은 이미 인가된 것이다. 예산 소비(P0-6)도
    여기서 일어난다 — 병렬 실행 중의 동시 소비 경합을 원천 제거(dispatch는 단일 지점).
    """

    def route_dispatch(state: SupervisorState):
        sends: list[Send] = []
        budget = state["budget"]
        for step in state["plan"].delegations:
            if not budget.can_delegate():
                # 캡 초과 → 남은 위임은 버리고 안전 종료(부분 결과로 종합) (P0-6)
                logger.info("supervisor_delegation_cap_reached", remaining=budget.remaining())
                break
            if not authorizer.is_delegation_allowed(state["allowed"], step.profile):
                # 스코프 밖 위임 즉시 스킵 (§0-3, P0-4) — 예산은 소비하지 않는다.
                logger.warning("supervisor_delegation_denied", profile=step.profile)
                continue
            budget.consume()
            sends.append(
                Send(
                    "delegate",
                    {
                        "current_step": step,
                        "ctx": state["ctx"],
                        "user_ctx": state["user_ctx"],
                        "trace": state["trace"],
                        "workflow_policy": state["workflow_policy"],
                        "round": state["round"],
                        "emitter": state.get("emitter"),
                        "stream_final": False,
                    },
                )
            )
        # 토큰 스트리밍: 이 위임의 답변이 곧 최종 답변으로 "확정"된 때만 서브 토큰을
        # 흘린다 — 단일 위임 + passthrough(on) + replan(off) + review(off) + 1라운드.
        # replan/review가 켜져 있으면 결과가 뒤집힐 수 있어(추가 라운드·reject) 서브
        # 토큰은 중간 산출물이다 — 그땐 finalize의 synthesize 스트림이 최종 생성자.
        if (
            len(sends) == 1
            and state.get("emitter") is not None
            and limits.single_passthrough
            and not limits.adaptive_replan
            and not limits.review_gate
            and state["round"] == 0
        ):
            sends[0].arg["stream_final"] = True
        return sends if sends else "finalize"

    return route_dispatch


def _create_delegate(runner: SubAgentRunner, limits: SupervisorLimits):
    """위임 1건 실행 태스크 — scoped context 파생 → 실행 → 결과/트레이스 수집.

    hub 강제(§0-5): 이 노드의 입력(`current_step`)은 dispatch 라우터가 메인의
    계획에서 만든 Send payload뿐이다. 서브 결과(SubAgentResult)에는 재라우팅
    필드가 계약상 없어, 서브가 다음 위임 대상을 정할 방법이 없다.
    인가/예산은 dispatch에서 이미 처리됐다(관문 통과분만 도달).
    """

    run_delegation = _create_run_delegation(runner, limits)

    async def _run_delegation_streaming(state: SupervisorState, step, sub_ctx) -> tuple:
        """stream_final 위임: 서브 토큰을 emitter로 중계하며 실행 (타임아웃 동일 적용).

        토큰 일부가 나간 뒤 실패하면 화면에 부분 텍스트가 남을 수 있다 —
        finalize의 degrade 답변이 done으로 내려가므로 사용자 최종 상태는 안전하다.
        """
        emitter = state["emitter"]
        timeout_sec = (
            limits.workflow_handoff_timeout_sec
            if state["workflow_policy"] == "handoff"
            else limits.delegation_timeout_sec
        )
        result = None
        forwarded = False
        try:
            async with asyncio.timeout(timeout_sec):
                async for event in runner.run_stream(
                    step.profile, step.subquery, sub_ctx,
                    user_security_level=state["user_ctx"].security_level_max,
                    tenant_id=state["ctx"].tenant_id,
                    trace=state["trace"],
                    workflow_policy=state["workflow_policy"],
                ):
                    if event["type"] in ("token", "replace"):
                        emitter.put_nowait((event["type"], event["data"]))
                        forwarded = True
                    elif event["type"] == "result":
                        result = event["data"]
        except TimeoutError:
            logger.warning(
                "supervisor_delegation_timeout", profile=step.profile, timeout_sec=timeout_sec,
            )
            result = SubAgentResult(
                profile=step.profile, answer="", ok=False, error="delegation_timeout",
            )
        if result is None:
            result = SubAgentResult(
                profile=step.profile, answer="", ok=False, error="stream_no_result",
            )
        # 워크플로우 핸드오프는 토큰 없이 result만 온다(버퍼드) → streamed 아님.
        return result, (forwarded and result.ok)

    async def delegate(state: SupervisorState) -> dict:
        step = state["current_step"]
        sub_ctx = derive_scoped_context(state["ctx"], step)  # 최소권한 (P0-5)

        started = time.monotonic()
        streamed = False
        if state.get("stream_final") and state.get("emitter") is not None:
            r, streamed = await _run_delegation_streaming(state, step, sub_ctx)
        else:
            r = await run_delegation(
                step, sub_ctx, state["user_ctx"], state["ctx"], state["trace"],
                state["workflow_policy"],
            )
        latency_ms = (time.monotonic() - started) * 1000
        # §0-5: 위임 경로 전부 관측 — 구조화 로그 + 트레이스 레코드(P1-3).
        logger.info(
            "supervisor_delegation_done",
            profile=step.profile,
            ok=r.ok,
            sources=len(r.sources),
            error=r.error,
            latency_ms=round(latency_ms, 1),
        )
        # 서브는 메인에만 반환 (§0-5, P0-8). reducer 채널이 병렬 태스크의 결과를
        # Send 생성 순서(=계획 순서)로 결정적으로 누적한다.
        update = {
            "results": [r],
            "delegation_log": [_delegation_trace_entry(step, r, latency_ms, state["round"])],
        }
        if streamed:
            update["streamed_answer"] = True
        return update

    return delegate


async def collect(state: SupervisorState) -> dict:
    """라운드 배리어(fan-in) — 이번 라운드의 병렬 위임이 전부 끝난 뒤 1회 실행."""
    return {"round": state["round"] + 1}


def _create_route_after_round(limits: SupervisorLimits):
    """라운드 종료 라우터 (P1-1) — 추가 위임 판단(replan)으로 갈지 종합으로 갈지."""

    def route_after_round(state: SupervisorState) -> str:
        if not limits.adaptive_replan:
            return "finalize"
        if state["round"] > limits.max_replan_rounds:
            return "finalize"
        if not state["budget"].can_delegate():
            return "finalize"
        results = state["results"]
        if not any(r.ok for r in results):
            # 전부 실패한 라운드는 재위임해도 같은 실패를 반복한다 — degrade 종합으로.
            return "finalize"
        if any(r.ok and r.workflow_handoff for r in results):
            # 인터랙티브 핸드오프는 즉시 passthrough — replan 대상이 아니다.
            return "finalize"
        return "replan"

    return route_after_round


def _create_replan(planner: SupervisorPlanner):
    """adaptive replan 노드 (P1-1) — 메인 LLM이 부족한 도메인의 추가 위임을 결정.

    hub 유지: 추가 위임도 메인의 결정이다(서브 요청이 아니라 결과를 본 메인의 판단).
    빈 계획이면 dispatch 라우터가 finalize로 보낸다. replan 라운드는 항상
    workflow_policy="block" — 인터랙티브 진입은 1라운드 단일 위임에서만 허용.
    """

    async def replan(state: SupervisorState) -> dict:
        new_plan = await planner.replan(state["question"], state["results"], state["candidates"])
        return {"plan": new_plan, "workflow_policy": "block"}

    return replan


def _create_finalize(planner: SupervisorPlanner, limits: SupervisorLimits):
    """종료 판정 — 핸드오프 passthrough / 워크플로우 차단 안내 / 검토 게이트 / synthesize."""

    async def _apply_review_gate(question: str, results: list[SubAgentResult]) -> list[SubAgentResult]:
        """P1-4 메인 검토 게이트 — 판정(pass/fail)만 하고 재생성하지 않는다.

        reject된 결과는 ok=False로 강등해 synthesize의 기존 degrade 경로
        (통과분만 종합 + 불완전 표시)를 그대로 태운다. 판정 실패는 fail-open.
        """
        ok_indices = [i for i, r in enumerate(results) if r.ok]
        if not ok_indices:
            return results
        verdicts = await asyncio.gather(
            *(planner.review(question, results[i]) for i in ok_indices),
            return_exceptions=True,
        )
        reviewed = list(results)
        for i, verdict in zip(ok_indices, verdicts):
            if isinstance(verdict, Exception):
                logger.warning(
                    "supervisor_review_gate_error", profile=results[i].profile, error=str(verdict)
                )
                continue  # fail-open — 게이트 장애가 답변 유실로 번지면 안 된다
            passed = bool(verdict.get("passed", True))
            note = str(verdict.get("note", ""))
            if passed:
                reviewed[i] = dataclasses.replace(results[i], review_passed=True, review_note=note)
            else:
                logger.info(
                    "supervisor_review_rejected", profile=results[i].profile, note=note
                )
                reviewed[i] = dataclasses.replace(
                    results[i], ok=False, error="review_rejected",
                    review_passed=False, review_note=note,
                )
        return reviewed

    async def finalize(state: SupervisorState) -> dict:
        results = state["results"]
        trace_info = _build_trace_info(state)

        # 워크플로우 핸드오프(단일 위임): 단계 응답을 그대로 전달 — synthesize 금지.
        # 다음 턴은 detect_sticky가 이 워크플로우로 이어붙인다.
        if len(results) == 1 and results[0].ok and results[0].workflow_handoff:
            r0 = results[0]
            logger.info("supervisor_workflow_handoff_started", profile=r0.profile)
            return {
                "response": AgentResponse(answer=r0.answer, sources=r0.sources, trace=trace_info)
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
                        trace=trace_info,
                    )
                }

        if limits.review_gate:
            results = await _apply_review_gate(state["question"], results)
            trace_info = TraceInfo(
                mode="supervisor",
                router_decision={
                    **trace_info.router_decision,
                    "review": [
                        {"profile": r.profile, "passed": r.review_passed, "note": r.review_note}
                        for r in results
                        if r.review_passed is not None
                    ],
                },
            )

        # Phase 3: 단일 위임 성공이면 synthesize 생략 — 라우팅=1위임 특수케이스 파리티.
        # 종합할 두 번째 근거가 없어 synthesize는 정보를 더하지 못하고 지연/변형만 남긴다.
        # 검토 게이트 뒤에 위치 — reject(ok=False)된 단일 결과는 통과하지 못한다.
        if limits.single_passthrough and len(results) == 1 and results[0].ok:
            r0 = results[0]
            logger.info("supervisor_single_passthrough", profile=r0.profile)
            return {
                "response": AgentResponse(answer=r0.answer, sources=r0.sources, trace=trace_info)
            }

        sources = []
        for r in results:
            if r.ok:
                sources.extend(r.sources)

        emitter = state.get("emitter")
        if emitter is not None:
            # 스트리밍: 종합 토큰을 그대로 흘리며 최종 답변을 조립한다(메인이 종합·소유).
            parts: list[str] = []
            async for token in planner.synthesize_stream(state["question"], results):
                parts.append(token)
                emitter.put_nowait(("token", token))
            return {
                "response": AgentResponse(answer="".join(parts), sources=sources, trace=trace_info),
                "streamed_answer": True,
            }

        answer = await planner.synthesize(state["question"], results)  # 메인이 종합·소유
        return {"response": AgentResponse(answer=answer, sources=sources, trace=trace_info)}

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

    체크포인터는 아직 붙이지 않는다(단일 요청 in-memory). 붙일 때
    상태 직렬화 경계를 함께 재설계한다.
    """
    workflow = StateGraph(SupervisorState)

    workflow.add_node("resolve_scope", _create_resolve_scope(authorizer, profile_store))
    workflow.add_node("detect_sticky", _create_detect_sticky(authorizer, workflow_engine))
    workflow.add_node("sticky_delegate", _create_sticky_delegate(runner, limits))
    workflow.add_node("decompose", _create_decompose(planner, limits))
    workflow.add_node("delegate", _create_delegate(runner, limits))
    workflow.add_node("collect", collect)
    workflow.add_node("replan", _create_replan(planner))
    workflow.add_node("finalize", _create_finalize(planner, limits))

    route_dispatch = _create_route_dispatch(authorizer, limits)
    route_after_round = _create_route_after_round(limits)

    workflow.add_edge(START, "resolve_scope")
    workflow.add_edge("resolve_scope", "detect_sticky")
    workflow.add_conditional_edges(
        "detect_sticky",
        route_by_sticky,
        {"sticky_delegate": "sticky_delegate", "decompose": "decompose"},
    )
    workflow.add_edge("sticky_delegate", END)
    # P1-2: dispatch가 Send 리스트를 반환하면 delegate 태스크들이 병렬 실행된다.
    workflow.add_conditional_edges("decompose", route_dispatch, ["delegate", "finalize"])
    workflow.add_edge("delegate", "collect")
    workflow.add_conditional_edges(
        "collect", route_after_round, {"replan": "replan", "finalize": "finalize"}
    )
    workflow.add_conditional_edges("replan", route_dispatch, ["delegate", "finalize"])
    workflow.add_edge("finalize", END)

    return workflow.compile()
