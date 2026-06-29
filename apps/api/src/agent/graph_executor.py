"""GraphExecutor: 모드별 LangGraph 그래프 선택 + 실행.

UniversalAgent를 대체. execute()/execute_stream() 인터페이스 유지.
모드별 실행 로직은 mixin 3개(workflow/deterministic/agentic)로 분리.
"""

import time
from typing import TYPE_CHECKING, AsyncIterator, Optional

from langchain_core.language_models import BaseChatModel

from src.agent.executors.workflow_executor import WorkflowExecutorMixin
from src.agent.executors.deterministic_executor import DeterministicExecutorMixin
from src.agent.executors.agentic_executor import AgenticExecutorMixin
from src.agent.graphs import build_deterministic_graph
from src.agent.graph_cache import GraphCache
from src.domain.models import AgentMode, AgentResponse, TraceInfo
from src.infrastructure.providers.base import LLMProvider
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import get_logger
from src.observability.trace_logger import RequestTrace
from src.domain.execution_plan import ExecutionPlan
from src.safety.base import Guardrail
from src.services.kms_graph_client import KmsGraphClient
from src.domain.agent_context import AgentContext
from src.tools.registry import ToolRegistry
from src.workflow.engine import WorkflowEngine

if TYPE_CHECKING:
    from src.config import Settings
    from src.infrastructure.providers.factory import ProviderFactory

logger = get_logger(__name__)


class GraphExecutor(WorkflowExecutorMixin, DeterministicExecutorMixin, AgenticExecutorMixin):
    """모드별 LangGraph 그래프를 선택하여 실행한다.

    - DETERMINISTIC: StateGraph (고정 Tool 순서)
    - AGENTIC: create_react_agent (LLM 자율 Tool 선택)
    """

    def __init__(
        self,
        main_llm: LLMProvider,
        tool_registry: ToolRegistry,
        guardrails: Optional[dict[str, Guardrail]] = None,
        chat_model: Optional[BaseChatModel] = None,
        workflow_engine: Optional[WorkflowEngine] = None,
        kms_graph_client: Optional[KmsGraphClient] = None,
        vector_store: Optional[VectorStore] = None,
        graph_cache: Optional[GraphCache] = None,
        provider_factory: Optional["ProviderFactory"] = None,
        settings: Optional["Settings"] = None,
    ):
        self._main_llm = main_llm
        self._registry = tool_registry
        self._guardrails = guardrails or {}
        self._chat_model = chat_model
        self._workflow_engine = workflow_engine
        self._graph_cache = graph_cache or GraphCache()
        # P0-2/3 모델 오버라이드 seam: profile.main_model alias를 실제 모델로 바꿔 그래프를 빌드한다.
        # provider_factory / settings 가 None 이면 기존 self._chat_model 경로로 완전 폴백(회귀 없음).
        self._provider_factory = provider_factory
        self._settings = settings

        # 결정론적 그래프 (한 번 컴파일, 재사용)
        det_graph = build_deterministic_graph(
            llm=main_llm,
            registry=tool_registry,
            guardrails=self._guardrails,
            kms_graph_client=kms_graph_client,
            vector_store=vector_store,
        )
        self._deterministic_app = det_graph.compile()

    def invalidate_graph_cache(self, profile_id: str | None = None) -> int:
        """프로필 변경 시 컴파일된 agentic 그래프 캐시를 무효화한다 (D14 부분).

        프로필에서 도구를 제거해도 캐시된 그래프가 TTL(2h)까지 옛 도구로
        동작하는 보안 구멍을 막는다. profile_id=None이면 전체 무효화.
        제거된 엔트리 수를 반환한다.
        """
        if profile_id is None:
            return self._graph_cache.invalidate_all()
        return self._graph_cache.invalidate(profile_id)

    async def _suppress_completed_workflow_reentry(
        self, plan: ExecutionPlan, session_id: str,
    ) -> None:
        """완료된 동일 워크플로우의 자동 재진입을 막는다.

        디스커버리 펀넬은 리포트 추천(reveal_cta 완료)으로 끝나는 일회성 리드인이다.
        완료 후 들어온 후속 메시지가 같은 워크플로우로 다시 분류되면(라우터는 세션
        상태를 모른다) `_run_workflow_step`이 완료 세션을 보고 처음부터 재시작 →
        주제·상황·이름·생일을 또 묻는 루프가 생긴다. 완료 세션이 이미 있으면
        워크플로우를 재실행하지 말고 일반 대화(AGENTIC)로 응답한다 — 묘묘와
        자유롭게 이어가고 리포트 CTA는 유지된다.
        """
        if plan.mode != AgentMode.WORKFLOW or not session_id or not self._workflow_engine:
            return
        session = await self._workflow_engine.get_session(session_id)
        if session and session.completed and session.workflow_id == plan.workflow_id:
            logger.info(
                "workflow_reentry_suppressed",
                layer="AGENT",
                workflow_id=plan.workflow_id,
                session_id=session_id,
            )
            plan.mode = AgentMode.AGENTIC
            plan.workflow_id = None

    async def execute(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str = "",
        trace: Optional[RequestTrace] = None,
        context: Optional[AgentContext] = None,
    ) -> AgentResponse:
        """ExecutionPlan 기반 실행."""
        # Orchestrator 직접 응답 (인사/잡담)
        if plan.direct_answer is not None:
            return AgentResponse(
                answer=plan.direct_answer,
                sources=[],
                trace=TraceInfo(mode="orchestrator"),
            )

        start_time = time.time()
        await self._suppress_completed_workflow_reentry(plan, session_id)

        if plan.mode == AgentMode.WORKFLOW:
            response = await self._execute_workflow(question, plan, session_id)
        elif plan.mode == AgentMode.AGENTIC:
            response = await self._execute_agentic(question, plan, session_id, trace=trace, context=context)
        else:
            response = await self._execute_deterministic(question, plan, session_id, trace=trace, context=context)

        total_ms = (time.time() - start_time) * 1000
        if response.trace:
            response.trace.latency_ms = total_ms
        if trace:
            trace.add_node("graph_execute", duration_ms=round(total_ms, 1))

        return response

    async def execute_stream(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str = "",
        trace: Optional[RequestTrace] = None,
        context: Optional[AgentContext] = None,
    ) -> AsyncIterator[dict]:
        """SSE 스트리밍 실행."""
        # Orchestrator 직접 응답 (인사/잡담)
        if plan.direct_answer is not None:
            yield {"type": "token", "data": plan.direct_answer}
            yield {"type": "done", "data": {"tools_called": [], "sources": []}}
            return

        start_time = time.time()
        await self._suppress_completed_workflow_reentry(plan, session_id)

        if plan.mode == AgentMode.WORKFLOW:
            async for event in self._stream_workflow(question, plan, session_id):
                yield event
        elif plan.mode == AgentMode.AGENTIC:
            async for event in self._stream_agentic(question, plan, session_id, trace=trace, context=context):
                yield event
        else:
            async for event in self._stream_deterministic(question, plan, session_id, trace=trace, context=context):
                yield event

        if trace:
            total_ms = (time.time() - start_time) * 1000
            trace.add_node("graph_stream", duration_ms=round(total_ms, 1))
