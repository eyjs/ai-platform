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

    async def run(
        self,
        profile_id: str,
        query: str,
        ctx: AgentContext,
        *,
        user_security_level: str,
        tenant_id: str,
        trace: Optional[object] = None,
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
            # P0 제약: 인터랙티브 워크플로우는 위임 불가.
            # 서브는 stateless 단발 호출인데 워크플로우는 멀티턴(입력 대기 pause)이라
            # 위임하면 응답 없이 잡히거나(행) 다음 턴 연속성이 끊긴다. 메인에 명시
            # 반환해 degrade(직접 챗봇 안내)로 처리한다. 해제는 P1 sticky delegation 소유.
            if getattr(plan, "mode", None) == AgentMode.WORKFLOW:
                workflow_id = getattr(plan, "workflow_id", None)
                logger.warning(
                    "subagent_workflow_delegation_unsupported",
                    profile_id=profile_id,
                    workflow_id=workflow_id,
                )
                return SubAgentResult(
                    profile=profile_id,
                    answer="",
                    ok=False,
                    error="workflow_delegation_unsupported",
                )

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
            )
        except Exception as e:  # noqa: BLE001 - 서브 실패는 삼키되 메인에 명시 반환(부분실패 degrade 입력)
            logger.error(
                "subagent_run_failed",
                profile_id=profile_id,
                error=str(e),
                exc_info=True,
            )
            return SubAgentResult(profile=profile_id, answer="", ok=False, error=str(e))
