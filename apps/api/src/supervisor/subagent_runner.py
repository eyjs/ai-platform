"""SubAgentRunner: 기존 프로파일 그래프 실행(직접 모드 1회분)을 캡슐화한다.

앵커: `src/gateway/routes/helpers.py::_prepare_chat`의 직접 모드 내부 3단
(profile_store.get → ai_router.route(skip_context_resolve=True) → agent.execute)과
동일한 컴포넌트·동일한 호출 방식을 그대로 재사용한다(§0-1 additive, §0-2 무변경).

이 러너는 서브 AI 내부(RAG·그래프)에 신규 로직을 침투시키지 않는다. 서브는
자신의 결과/실패만 반환하고(§0-5 hub 강제), 다음 행동 결정은 메인(Supervisor)이 한다.
"""

from __future__ import annotations

from typing import Optional

from src.agent.graph_executor import GraphExecutor
from src.agent.profile_store import ProfileStore
from src.domain.agent_context import AgentContext
from src.domain.models import AgentMode
from src.observability.logging import get_logger
from src.router.ai_router import AIRouter
from src.supervisor.models import SubAgentResult
from src.tools.registry import ToolRegistry

logger = get_logger(__name__)


class SubAgentRunner:
    """서브 프로파일 실행을 "호출 가능한 능력"으로 감싼다.

    생성자 주입은 합성 루트(task-002)가 배선한다.
    """

    def __init__(
        self,
        profile_store: ProfileStore,
        ai_router: AIRouter,
        agent: GraphExecutor,
        tool_registry: ToolRegistry,
    ):
        self._profile_store = profile_store
        self._ai_router = ai_router
        self._agent = agent
        self._tool_registry = tool_registry

    async def _route_and_guard(
        self,
        profile,
        query: str,
        ctx: AgentContext,
        *,
        user_security_level: str,
        tenant_id: str,
        workflow_policy: str,
    ) -> tuple[object | None, bool, SubAgentResult | None]:
        """공통 전처리: 라우팅 → 워크플로우 위임 정책 가드.

        반환: (plan, is_workflow, 차단결과). 차단결과가 있으면 실행 없이 그대로 반환한다.

        인터랙티브 워크플로우 위임 정책:
        - "block"(기본, 다중 위임): stateless 단발 위임과 멀티턴 워크플로우는
          불일치 → 실행 없이 실패 반환(오라우팅된 워크플로우가 다른 위임을 오염 방지).
        - "handoff"(단일 위임/sticky): 워크플로우를 스코프 세션에서 시작/진행하고
          그 단계 질문을 그대로 반환 — 메인이 passthrough + 다음 턴 sticky 감지.
        """
        tools = self._tool_registry.resolve(profile.tool_names)
        plan = await self._ai_router.route(
            query=query,
            profile=profile,
            tools=tools,
            history=ctx.conversation_history,
            user_security_level=user_security_level,
            skip_context_resolve=True,
            external_context="",
            tenant_id=tenant_id,
            session_scope_id=None,
        )
        is_workflow = getattr(plan, "mode", None) == AgentMode.WORKFLOW
        if is_workflow and workflow_policy != "handoff":
            logger.warning(
                "subagent_workflow_delegation_unsupported",
                profile_id=profile.id,
                workflow_id=getattr(plan, "workflow_id", None),
            )
            blocked = SubAgentResult(
                profile=profile.id,
                answer="",
                ok=False,
                error="workflow_delegation_unsupported",
            )
            return plan, is_workflow, blocked
        if is_workflow:
            logger.info(
                "subagent_workflow_handoff",
                profile_id=profile.id,
                workflow_id=getattr(plan, "workflow_id", None),
                session_id=ctx.session_id,
            )
        return plan, is_workflow, None

    async def run(
        self,
        profile_id: str,
        query: str,
        ctx: AgentContext,
        *,
        user_security_level: str,
        tenant_id: str,
        trace: Optional[object] = None,
        workflow_policy: str = "block",
    ) -> SubAgentResult:
        """단일 서브 프로파일을 실행하고 결과/실패를 메인에 반환한다.

        방어적 계약: `profile_id`는 **이미 인가된 값만** 들어온다는 전제로 동작한다.
        인가 재검사(deny-by-default)는 이 함수가 아니라 호출 직전 위임 루프
        (task-003)가 task-004 관문으로 수행한다. 이 함수 안에서는 인가를 하지 않는다.

        서브는 stateless로 호출된다(§6-5) — 세션 메모리 write나 워크플로우
        재개는 이 경로에서 수행하지 않는다.
        """
        profile = await self._profile_store.get(profile_id)
        if not profile:
            return SubAgentResult(profile=profile_id, answer="", ok=False, error="profile_not_found")

        try:
            plan, is_workflow, blocked = await self._route_and_guard(
                profile, query, ctx,
                user_security_level=user_security_level,
                tenant_id=tenant_id,
                workflow_policy=workflow_policy,
            )
            if blocked is not None:
                return blocked

            resp = await self._agent.execute(
                question=query,
                plan=plan,
                session_id=ctx.session_id,
                trace=trace,
                context=ctx,
            )
            return SubAgentResult(
                profile=profile_id,
                answer=resp.answer,
                sources=resp.sources,
                trace=resp.trace,
                ok=True,
                workflow_handoff=is_workflow,
            )
        except Exception as e:  # noqa: BLE001 - 서브 실패는 삼키되 메인에 명시 반환(부분실패 degrade 입력)
            logger.error(
                "subagent_run_failed",
                profile_id=profile_id,
                error=str(e),
                exc_info=True,
            )
            return SubAgentResult(profile=profile_id, answer="", ok=False, error=str(e))

    async def run_stream(
        self,
        profile_id: str,
        query: str,
        ctx: AgentContext,
        *,
        user_security_level: str,
        tenant_id: str,
        trace: Optional[object] = None,
        workflow_policy: str = "block",
    ):
        """run()의 토큰 스트리밍 판 — 같은 가드·같은 결과 계약.

        yield 이벤트: {"type": "token"|"replace", "data": str} 진행 중,
        마지막에 반드시 {"type": "result", "data": SubAgentResult} 1건.
        워크플로우 핸드오프는 단계 질문이 짧아 토큰 없이 result만 낸다(버퍼드).
        hub 계약 동일: 결과에 재라우팅 정보 없음, 서브는 메인에만 반환(§0-5).
        """
        profile = await self._profile_store.get(profile_id)
        if not profile:
            yield {"type": "result", "data": SubAgentResult(
                profile=profile_id, answer="", ok=False, error="profile_not_found",
            )}
            return

        try:
            plan, is_workflow, blocked = await self._route_and_guard(
                profile, query, ctx,
                user_security_level=user_security_level,
                tenant_id=tenant_id,
                workflow_policy=workflow_policy,
            )
            if blocked is not None:
                yield {"type": "result", "data": blocked}
                return

            if is_workflow:
                resp = await self._agent.execute(
                    question=query, plan=plan, session_id=ctx.session_id,
                    trace=trace, context=ctx,
                )
                yield {"type": "result", "data": SubAgentResult(
                    profile=profile_id, answer=resp.answer, sources=resp.sources,
                    trace=resp.trace, ok=True, workflow_handoff=True,
                )}
                return

            answer_parts: list[str] = []
            sources: list = []
            async for event in self._agent.execute_stream(
                question=query, plan=plan, session_id=ctx.session_id,
                trace=trace, context=ctx,
            ):
                event_type = event.get("type")
                if event_type == "token":
                    answer_parts.append(event["data"])
                    yield {"type": "token", "data": event["data"]}
                elif event_type == "replace":
                    answer_parts.clear()
                    answer_parts.append(event["data"])
                    yield {"type": "replace", "data": event["data"]}
                elif event_type == "done":
                    sources = event.get("data", {}).get("sources", [])
                # thinking/trace 이벤트는 서브 내부 관측 — 위임 경로에선 중계하지 않는다.

            yield {"type": "result", "data": SubAgentResult(
                profile=profile_id, answer="".join(answer_parts), sources=sources, ok=True,
            )}
        except Exception as e:  # noqa: BLE001 - 서브 실패는 삼키되 메인에 명시 반환(부분실패 degrade 입력)
            logger.error(
                "subagent_run_stream_failed",
                profile_id=profile_id,
                error=str(e),
                exc_info=True,
            )
            yield {"type": "result", "data": SubAgentResult(
                profile=profile_id, answer="", ok=False, error=str(e),
            )}
