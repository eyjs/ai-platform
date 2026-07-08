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
from src.supervisor.models import SubAgentResult, SupervisorLimits
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
    ) -> None:
        self._planner = planner
        self._runner = runner
        self._authorizer = authorizer
        self._limits = limits
        self._profile_store = profile_store

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

        plan = await self._planner.decompose(question, allowed, candidates)  # (P0-3)

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

            # 단일 관문: runner.run 호출은 반드시 is_delegation_allowed 통과 블록 안에만 존재한다.
            # 위임 타임아웃: 서브가 응답 없이 잡히면(외부 의존 행 등) supervise 전체가
            # 무한 대기하며 SSE가 ping만 보내는 사고를 차단한다(P0-6 확장).
            try:
                async with asyncio.timeout(self._limits.delegation_timeout_sec):
                    r = await self._runner.run(
                        step.profile,
                        step.subquery,
                        sub_ctx,
                        user_security_level=user_ctx.security_level_max,
                        tenant_id=ctx.tenant_id,
                        trace=trace,
                    )
            except TimeoutError:
                logger.warning(
                    "supervisor_delegation_timeout",
                    profile=step.profile,
                    timeout_sec=self._limits.delegation_timeout_sec,
                )
                r = SubAgentResult(
                    profile=step.profile, answer="", ok=False, error="delegation_timeout",
                )
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
