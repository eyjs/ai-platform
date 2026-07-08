"""Supervisor: 최소 위임 루프 (decompose → 순차 위임 → synthesize) (P0-3).

requirement §5 의사코드를 P0 범위(순차, replan/review 제외)로 구현한다.
루프는 오직 `plan.delegations`(메인이 만든 계획)만 순회하며(hub 강제, §0-5),
`runner.run` 호출은 `is_delegation_allowed` 관문을 통과한 블록 안에만 존재한다(§0-3).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from src.config import settings
from src.domain.agent_context import AgentContext
from src.domain.models import AgentResponse
from src.observability.logging import get_logger
from src.supervisor.authz import DelegationAuthorizer
from src.supervisor.limits import DelegationBudget
from src.supervisor.models import DelegationStep, SubAgentResult, SupervisorLimits
from src.supervisor.planner_llm import SupervisorPlanner
from src.supervisor.scoped_context import derive_scoped_context
from src.supervisor.subagent_runner import SubAgentRunner

logger = get_logger(__name__)


class Supervisor:
    """decompose → (재검사 → scoped context 파생 → 위임) → synthesize 순차 루프.

    생성자 주입은 합성 루트(task-002)가 배선한다.
    """

    def __init__(
        self,
        planner: SupervisorPlanner,
        runner: SubAgentRunner,
        authorizer: DelegationAuthorizer,
        limits: SupervisorLimits,
        profile_store,
        workflow_engine=None,
    ) -> None:
        self._planner = planner
        self._runner = runner
        self._authorizer = authorizer
        self._limits = limits
        self._profile_store = profile_store
        # sticky 감지용(진행 중 서브 워크플로우 우선 재위임). 미주입 시 sticky 비활성.
        self._workflow_engine = workflow_engine

    async def _run_delegation(
        self,
        step,
        sub_ctx: AgentContext,
        user_ctx,
        ctx: AgentContext,
        trace,
        workflow_policy: str,
    ) -> SubAgentResult:
        """위임 1건 실행 — 타임아웃 상한 공용 적용(P0-6 확장).

        서브가 응답 없이 잡히면(외부 의존 행 등) supervise 전체가 무한 대기하며
        SSE가 ping만 보내는 사고를 차단한다. 워크플로우 핸드오프는 dynamic 스텝의
        로컬 LLM 생성이 오래 걸려(실측 50~120s+) 별도의 넉넉한 상한을 쓴다.
        """
        timeout_sec = (
            self._limits.workflow_handoff_timeout_sec
            if workflow_policy == "handoff"
            else self._limits.delegation_timeout_sec
        )
        try:
            async with asyncio.timeout(timeout_sec):
                return await self._runner.run(
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

    async def _find_sticky_workflow(self, ctx: AgentContext, allowed, all_profiles, supervisor_id):
        """진행 중인 서브 워크플로우 탐지 (sticky delegation).

        워크플로우 세션은 스코프 세션 id(`{parent}::sub::{profile}`)로 결정적으로
        파생되므로, 별도 상태 저장 없이 workflow engine이 곧 정본이다.
        결정권은 메인에 있다 — 서브가 요청하는 게 아니라 메인이 감지해 재위임한다(§0-5).
        """
        if not self._workflow_engine:
            return None
        for p in all_profiles:
            if p.id == supervisor_id or not self._authorizer.is_delegation_allowed(allowed, p.id):
                continue
            scoped_id = f"{ctx.session_id}::sub::{p.id}"
            try:
                session = await self._workflow_engine.get_session(scoped_id)
            except Exception:  # noqa: BLE001 - sticky 탐지 실패는 일반 경로로 degrade
                continue
            if session and not getattr(session, "completed", True):
                return p.id
        return None

    async def supervise(
        self,
        question: str,
        ctx: AgentContext,
        user_ctx,
        trace: Optional[object] = None,
    ) -> AgentResponse:
        """질의를 분해해 인가된 서브에 순차 위임하고 결과를 종합한다."""
        # task-002가 추가할 설정값. 아직 없는 워크트리에서도 안전하게 동작하도록
        # 방어적으로 읽는다(기본값 "supervisor").
        supervisor_id = getattr(settings, "supervisor_profile_id", "supervisor")

        allowed = await self._authorizer.resolve_allowed(user_ctx)  # deny-by-default (P0-4)

        all_profiles = await self._profile_store.list_all()
        candidates = [
            {"id": p.id, "name": p.name, "description": p.description}
            for p in all_profiles
            if p.id != supervisor_id and self._authorizer.is_delegation_allowed(allowed, p.id)
        ]

        # sticky delegation: 진행 중인 서브 워크플로우가 있으면 decompose를 건너뛰고
        # 그 서브로 직행한다(멀티턴 연속성 — "위임 → 진행 → 완료 후 통합 복귀" 루프).
        # 오라우팅 방지: decompose가 매 턴 맥락 없이 재분류해 생년월일 답변 등이
        # 엉뚱한 서브로 새는 문제를 이 감지가 차단한다.
        sticky_profile = await self._find_sticky_workflow(ctx, allowed, all_profiles, supervisor_id)
        if sticky_profile:
            step = DelegationStep(
                profile=sticky_profile, subquery=question, reason="진행 중 워크플로우 연속(sticky)",
            )
            sub_ctx = derive_scoped_context(ctx, step)
            r = await self._run_delegation(step, sub_ctx, user_ctx, ctx, trace, "handoff")
            logger.info(
                "supervisor_workflow_sticky",
                profile=sticky_profile,
                ok=r.ok,
                error=r.error,
            )
            if r.ok:
                # 워크플로우 단계 응답은 그대로 전달(passthrough) — synthesize가
                # 단계 질문("생년월일을 알려주세요")을 훼손하면 안 된다.
                return AgentResponse(answer=r.answer, sources=r.sources, trace=trace)
            # sticky 실패 시 decompose로 폴백하지 않는다 — 워크플로우 중간 발화
            # ("투자" 등)를 단독 질문으로 재해석하면 무의미한 답이 나온다(실사고).
            # 워크플로우 세션은 보존되므로 재시도하면 sticky가 이어붙는다.
            return AgentResponse(
                answer=(
                    "진행 중인 상담 응답이 지연되고 있어요. 잠시 후 같은 대화에서 "
                    "이어서 말씀해 주시면 계속 진행할게요."
                ),
                sources=[],
                trace=trace,
            )

        plan = await self._planner.decompose(question, allowed, candidates)  # (P0-3)

        # 단일 위임이면 워크플로우 핸드오프 허용(사주 등 인터랙티브 진입),
        # 다중 위임이면 차단(오라우팅된 워크플로우가 종합을 오염/지연시키는 것 방지).
        workflow_policy = "handoff" if len(plan.delegations) == 1 else "block"

        budget = DelegationBudget(self._limits)  # (P0-6)
        results: list[SubAgentResult] = []

        for step in plan.delegations:
            if not budget.can_delegate():
                # 캡 초과 → 안전 종료(부분 결과로 종합) (P0-6)
                logger.info("supervisor_delegation_cap_reached", remaining=budget.remaining())
                break

            if not self._authorizer.is_delegation_allowed(allowed, step.profile):
                # 스코프 밖 위임 즉시 스킵 (§0-3, P0-4)
                logger.warning("supervisor_delegation_denied", profile=step.profile)
                continue

            sub_ctx = derive_scoped_context(ctx, step)  # 최소권한 (P0-5)
            budget.consume()

            # 단일 관문: runner.run 호출은 반드시 is_delegation_allowed 통과 블록 안에만
            # 존재한다(_run_delegation이 타임아웃 상한과 함께 감싼다).
            r = await self._run_delegation(step, sub_ctx, user_ctx, ctx, trace, workflow_policy)
            results.append(r)  # 서브는 메인에만 반환 (§0-5, P0-8)
            # §0-5: 위임 경로 전부 관측 — 성공/실패 위임을 운영자 트레이스로 남긴다.
            # (계층 트레이스 노드는 P1-3 소유. P0는 구조화 로그로 최소 관측 보장.)
            logger.info(
                "supervisor_delegation_done",
                profile=step.profile,
                ok=r.ok,
                sources=len(r.sources),
                error=r.error,
            )
            # P0: adaptive replan 없음(is_adaptive 항상 False). review 게이트 없음(P1).

        # 워크플로우 핸드오프(단일 위임): 단계 응답을 그대로 전달 — synthesize 금지.
        # 다음 턴은 위의 sticky 감지가 이 워크플로우로 이어붙인다.
        if len(results) == 1 and results[0].ok and results[0].workflow_handoff:
            r0 = results[0]
            logger.info("supervisor_workflow_handoff_started", profile=r0.profile)
            return AgentResponse(answer=r0.answer, sources=r0.sources, trace=trace)

        # 워크플로우 위임 차단으로 전부 실패한 경우: 일반 폴백 대신
        # "해당 챗봇을 직접 이용" 안내 — 사용자가 다음 행동을 알 수 있게(degrade UX).
        if results and not any(r.ok for r in results):
            wf_blocked = [r for r in results if r.error == "workflow_delegation_unsupported"]
            if wf_blocked:
                name_by_id = {p.id: p.name for p in all_profiles}
                names = ", ".join(f"'{name_by_id.get(r.profile, r.profile)}'" for r in wf_blocked)
                return AgentResponse(
                    answer=(
                        f"요청하신 작업은 단계별 대화(정보 입력)가 필요해 통합 창구에서 "
                        f"바로 처리해 드리기 어렵습니다. {names} 챗봇을 직접 선택해 이용해 주세요."
                    ),
                    sources=[],
                    trace=trace,
                )

        answer = await self._planner.synthesize(question, results)  # 메인이 종합·소유

        sources = []
        for r in results:
            if r.ok:
                sources.extend(r.sources)

        return AgentResponse(answer=answer, sources=sources, trace=trace)
