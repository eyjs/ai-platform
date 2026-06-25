"""GraphExecutor: 모드별 LangGraph 그래프 선택 + 실행.

UniversalAgent를 대체. execute()/execute_stream() 인터페이스 유지.
"""

import time
from typing import TYPE_CHECKING, AsyncIterator, Optional

from langchain_core.language_models import BaseChatModel

from src.agent.graphs import build_agentic_graph, build_deterministic_graph
from src.agent.nodes import build_prompt, build_source_dicts, run_guardrail_chain
from src.agent.state import create_initial_state
from src.agent.tool_adapter import convert_tools_to_langchain
from src.domain.models import AgentMode, AgentResponse, SourceRef, TraceInfo
from src.infrastructure.providers.base import LLMProvider, StreamChunk
from src.infrastructure.providers.model_aliases import resolve_model_alias
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import get_logger
from src.observability.trace_logger import RequestTrace
from src.router.execution_plan import ExecutionPlan
from src.router.graph_cache import GraphCache
from src.safety.base import GuardrailContext
from src.safety.base import Guardrail
from src.services.kms_graph_client import KmsGraphClient
from src.domain.agent_context import AgentContext
from src.tools.registry import ToolRegistry
from src.workflow.engine import StepResult, WorkflowEngine

if TYPE_CHECKING:
    from src.config import Settings
    from src.infrastructure.providers.factory import ProviderFactory

logger = get_logger(__name__)


def _content_to_text(content) -> str:
    """LangChain 메시지 content 를 평문 텍스트로 평탄화한다.

    `AIMessageChunk.content` 는 모델에 따라 `str` 또는 content-block
    리스트(`list[str | dict]`, 예: `[{"type": "text", "text": "..."}]`)로
    반환된다. 리스트가 그대로 토큰 스트림에 흘러가면
    - 프론트엔드에서 `[object Object]` 로 렌더되고
    - `answer += content` (str + list) 에서 TypeError 가 발생한다.
    여기서 항상 str 로 정규화하여 두 문제를 차단한다.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return "" if content is None else str(content)


def _extract_faithfulness_score(guardrail_results: dict) -> Optional[float]:
    """guardrail_results 에서 faithfulness guard 의 수치 스코어를 추출한다.

    Task 014: api_request_logs.faithfulness_score 저장용.
    None 이 기본 (측정 불가, 또는 guard 미실행).
    """
    if not guardrail_results:
        return None
    entry = guardrail_results.get("faithfulness")
    if isinstance(entry, dict):
        score = entry.get("score")
        if isinstance(score, (int, float)):
            return float(score)
    return None


def _build_agentic_user_turn(question: str, plan: "ExecutionPlan") -> str:
    """에이전틱 user 턴 봉투를 구성한다.

    volatile(날짜+directive)과 이전 대화 기록을 user 턴에 주입한다.
    캐시된 system prefix(페르소나+grounding) 뒤에 붙으므로 prefix 캐시를 깨지 않으면서
    매턴 최신 날짜/지시를 전달한다(컴파일 그래프엔 volatile 미포함 → byte-stable).
    """
    prefix_parts: list[str] = []
    if plan.volatile_system_prompt:
        prefix_parts.append(f"[지침]\n{plan.volatile_system_prompt}")
    if plan.conversation_context:
        prefix_parts.append(f"[이전 대화 기록]\n{plan.conversation_context}")
    if not prefix_parts:
        return question
    return "\n\n".join(prefix_parts + [f"[현재 질문]\n{question}"])


class GraphExecutor:
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

    # --- 워크플로우 모드 ---

    async def _execute_workflow(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
    ) -> AgentResponse:
        """워크플로우 모드 실행. StepResult → AgentResponse 변환."""
        if not self._workflow_engine:
            logger.warning("workflow_engine_missing, falling back to deterministic")
            return AgentResponse(
                answer="워크플로우 엔진이 초기화되지 않았습니다.",
                sources=[],
                trace=TraceInfo(mode="workflow"),
            )

        step_result = await self._run_workflow_step(question, plan, session_id)
        return self._step_result_to_response(step_result, plan)

    async def _stream_workflow(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """워크플로우 모드 스트리밍. 워크플로우는 즉시 응답이므로 한 번에 전송."""
        if not self._workflow_engine:
            yield {"type": "token", "data": "워크플로우 엔진이 초기화되지 않았습니다."}
            yield {"type": "done", "data": {"tools_called": [], "sources": []}}
            return

        step_result = await self._run_workflow_step(question, plan, session_id)

        yield {"type": "trace", "data": {
            "step": "workflow",
            "workflow_id": plan.workflow_id,
            "step_id": step_result.step_id,
            "step_type": step_result.step_type,
            "completed": step_result.completed,
            "escaped": step_result.escaped,
        }}
        # 선택지가 있으면 메시지에 번호 목록 추가
        message = step_result.bot_message
        if step_result.options:
            options_text = "\n".join(
                f"{i+1}. {opt}" for i, opt in enumerate(step_result.options)
            )
            message = f"{message}\n\n{options_text}"
        # 워크플로우 진행 중이면 나가기 안내 추가
        if not step_result.completed and not step_result.escaped:
            message += '\n\n_(\"나가기\" 또는 \"취소\"를 입력하면 워크플로우를 종료합니다)_'
        yield {"type": "token", "data": message}
        # 워크플로우 경로 usage: StepResult에 usage 정보가 있으면 포함 (best-effort)
        workflow_done_data: dict = {
            "tools_called": [],
            "sources": [],
            "workflow": {
                "options": step_result.options,
                "step_id": step_result.step_id,
                "step_type": step_result.step_type,
                "collected": step_result.collected,
                "completed": step_result.completed,
                "escaped": step_result.escaped,
                "report": step_result.report,
                # ── 신규(v2): 구조 신호 (saju 구조-우선 매핑) ──
                "intent_confirm": step_result.intent_confirm or None,
                "collection": step_result.collection or None,  # 수집스텝: 빌더가 채움. 빈dict→None
                "concluded": step_result.concluded,
            },
        }
        _wf_usage = getattr(step_result, "usage", None)
        if _wf_usage and isinstance(_wf_usage, dict) and any(_wf_usage.values()):
            workflow_done_data["usage"] = _wf_usage
        yield {"type": "done", "data": workflow_done_data}

    async def _run_workflow_step(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
    ) -> StepResult:
        """워크플로우 시작 또는 진행."""
        engine = self._workflow_engine
        session = await engine.get_session(session_id)

        if not session or session.completed:
            # 새 워크플로우 시작
            workflow_id = plan.workflow_id
            if not workflow_id:
                return StepResult(
                    bot_message="워크플로우 ID가 지정되지 않았습니다.",
                    completed=True,
                )
            logger.info(
                "workflow_start_via_chat",
                layer="AGENT",
                workflow_id=workflow_id,
                session_id=session_id,
            )
            return await engine.start(
                workflow_id, session_id,
                context_adapter=plan.context_adapter,
                cache_padding_text=plan.cache_padding_text,
            )

        # 기존 세션 진행
        return await engine.advance(session_id, question)

    @staticmethod
    def _step_result_to_response(
        step_result: StepResult,
        plan: ExecutionPlan,
    ) -> AgentResponse:
        """StepResult를 AgentResponse로 변환."""
        # 선택지가 있으면 메시지에 번호 목록 추가
        answer = step_result.bot_message
        if step_result.options:
            options_text = "\n".join(
                f"{i+1}. {opt}" for i, opt in enumerate(step_result.options)
            )
            answer = f"{answer}\n\n{options_text}"

        return AgentResponse(
            answer=answer,
            sources=[],
            trace=TraceInfo(
                question_type=plan.question_type.value if plan.question_type else "",
                mode="workflow",
                tools_called=[],
                router_decision={
                    "workflow_id": plan.workflow_id,
                    "step_id": step_result.step_id,
                    "step_type": step_result.step_type,
                    "completed": step_result.completed,
                    "escaped": step_result.escaped,
                },
            ),
        )

    # --- 결정론적 모드 ---

    async def _execute_deterministic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
        trace: Optional[RequestTrace] = None,
        context: Optional[AgentContext] = None,
    ) -> AgentResponse:
        initial_state = create_initial_state(question, plan, session_id, trace=trace)
        result = await self._deterministic_app.ainvoke(initial_state)

        tools_called = result.get("tools_called", [])
        sources = [SourceRef(**s) for s in result.get("sources", [])]
        guardrail_score = _extract_faithfulness_score(result.get("guardrail_results") or {})

        return AgentResponse(
            answer=result.get("answer", ""),
            sources=sources,
            trace=TraceInfo(
                question_type=plan.question_type.value,
                mode=plan.mode.value,
                tools_called=tools_called,
            ),
            guardrail_score=guardrail_score,
        )

    async def _stream_deterministic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
        trace: Optional[RequestTrace] = None,
        context: Optional[AgentContext] = None,
    ) -> AsyncIterator[dict]:
        """결정론적 모드 스트리밍.

        is_streaming=True로 그래프를 실행하여 Tool 노드만 실제 작업하고,
        generate/guardrails/build_response 노드는 바이패스한다.
        LLM 토큰 스트리밍과 Guardrail은 래퍼에서 직접 처리한다.

        TODO(tech-debt): Checkpointer 도입 시 상태 영속성 구멍 해결 필요.
        현재 is_streaming=True에서는 AgentState의 answer/sources가 빈 상태로
        남는다. 대안 B(BaseChatModel 네이티브 스트리밍) 전환 또는
        스트리밍 완료 후 graph.update_state() 명시 호출로 해결할 것.
        """
        # RAG 불필요 -> 직접 스트리밍
        if not plan.strategy.needs_rag:
            async for chunk in self._main_llm.generate_stream_typed(
                question, system=plan.system_prompt,
            ):
                if chunk.kind == "thinking":
                    yield {"type": "thinking", "data": chunk.content}
                else:
                    yield {"type": "token", "data": chunk.content}
            yield {"type": "done", "data": {"tools_called": [], "sources": []}}
            return

        # Tool 실행만 수행 (is_streaming=True → LLM/Guardrail 노드 바이패스)
        yield {"type": "trace", "data": {"step": "tool_execution", "status": "start"}}

        initial_state = create_initial_state(
            question, plan, session_id, is_streaming=True, trace=trace,
        )
        tools_called = []
        search_results = []

        async for chunk in self._deterministic_app.astream(
            initial_state, stream_mode="updates",
        ):
            for node_name, state_update in chunk.items():
                if not state_update:
                    continue
                if node_name == "plan_execution":
                    planned_steps = state_update.get("planned_steps", [])
                    planning_reasoning = state_update.get("planning_reasoning", "")
                    if planned_steps:
                        yield {"type": "trace", "data": {
                            "step": "planning",
                            "steps_count": len(planned_steps),
                            "reasoning": planning_reasoning,
                        }}
                elif node_name == "execute_tools":
                    tools_called = state_update.get("tools_called", [])
                    search_results = state_update.get("search_results", [])
                    for tl in state_update.get("tool_latencies", []):
                        yield {"type": "trace", "data": {
                            "tool": tl["tool"],
                            "success": tl["success"],
                            "ms": tl["ms"],
                        }}
                elif node_name == "graph_enrich":
                    if "search_results" in state_update:
                        search_results = state_update["search_results"]
                    enrichment = state_update.get("graph_enrichment", {})
                    if enrichment.get("enriched") or enrichment.get("discovered"):
                        yield {"type": "trace", "data": {
                            "step": "graph_enrich",
                            "enriched": enrichment.get("enriched", 0),
                            "discovered": enrichment.get("discovered", 0),
                        }}

        # LLM 토큰 스트리밍 (래퍼에서 직접 처리)
        # graph_enrich 결과가 뒤에 추가되므로 score 기준 정렬 후 슬라이스
        search_results.sort(key=lambda r: r.get("score", 0), reverse=True)
        prompt_results = search_results[:plan.strategy.max_vector_chunks]
        prompt = build_prompt(question, plan, prompt_results)

        yield {"type": "trace", "data": {
            "step": "generation", "status": "start",
            "context_chunks": len(prompt_results),
        }}

        answer_tokens = []
        # thinking/answer 분리 스트리밍 (base 기본 구현은 전부 answer)
        async for chunk in self._main_llm.generate_stream_typed(
            prompt, system=plan.system_prompt,
        ):
            if chunk.kind == "thinking":
                yield {"type": "thinking", "data": chunk.content}
            else:
                answer_tokens.append(chunk.content)
                yield {"type": "token", "data": chunk.content}

        # Guardrail (래퍼에서 직접 처리)
        full_answer = "".join(answer_tokens)
        faithfulness_score: Optional[float] = None
        if plan.guardrail_chain:
            guardrail_ctx = GuardrailContext(
                question=question,
                source_documents=search_results,
                profile_id=session_id,
                response_policy=plan.response_policy,
            )
            modified, results = await run_guardrail_chain(
                full_answer, plan.guardrail_chain, self._guardrails, guardrail_ctx,
            )
            if modified != full_answer:
                yield {"type": "trace", "data": {"step": "guardrail_modified", "results": results}}
                yield {"type": "replace", "data": modified}
            # Task 014: faithfulness 스코어 포집 → done 이벤트 동봉
            faithfulness_score = _extract_faithfulness_score(results)

        sources = build_source_dicts(search_results)
        done_data: dict = {
            "tools_called": tools_called,
            "sources": sources,
        }
        if faithfulness_score is not None:
            done_data["faithfulness_score"] = faithfulness_score
        yield {
            "type": "done",
            "data": done_data,
        }

    # --- 에이전틱 모드 ---

    def _get_or_build_agentic_graph(self, lc_tools: list, plan: ExecutionPlan):
        """캐시된 agentic graph를 반환하거나 새로 빌드한다."""
        tool_names = [t.name for t in lc_tools]
        cache_key = GraphCache.make_key(plan.system_prompt, tool_names, plan.cache_padding_text)

        cached = self._graph_cache.get(cache_key)
        if cached is not None:
            logger.debug(
                "agentic_graph_cache_hit",
                tool_names=tool_names,
            )
            return cached

        agent_app = build_agentic_graph(
            chat_model=self._chat_model,
            tools=lc_tools,
            system_prompt=plan.system_prompt,
            cache_padding_text=plan.cache_padding_text,
        )

        # profile_id로 엔트리 태깅 → 프로필 변경 시 invalidate(profile_id)가 실제로 매칭.
        # (이전엔 ExecutionPlan에 profile_id가 없어 항상 None → targeted invalidation 무효였음)
        self._graph_cache.put(cache_key, agent_app, profile_id=plan.profile_id or None)

        logger.debug(
            "agentic_graph_cache_miss",
            tool_names=tool_names,
            cache_size=self._graph_cache.size,
        )
        return agent_app

    def _effective_agentic_app(
        self,
        lc_tools: list,
        plan: ExecutionPlan,
        context: "AgentContext",
    ):
        """plan.main_model alias를 해석해 적절한 agentic graph app을 반환한다.

        오버라이드 로직 (P0-2/3 model wiring seam):
          1. plan.main_model 이 지정됐고 settings/provider_factory 가 있으면
             alias를 구체 모델 ID로 해석한다.
          2. resolved 가 비어있으면 기존 self._chat_model 경로로 폴백한다.
          3. context.metadata 가 있으면 캐시 없이 새로 빌드한다(기존 동작 유지).

        Task C (timeout/cap) seam: 이 메서드의 반환값(agent_app)을 래핑하거나
        build_agentic_graph 호출 직전에 추가 인자를 주입하면 된다.
        """
        # --- P0-2/3 오버라이드 경로 ---
        resolved = ""
        if self._settings and self._provider_factory and plan.main_model:
            resolved = resolve_model_alias(plan.main_model, self._settings)

        if resolved:
            # 모델별 그래프는 공유 캐시(system_prompt+tools+padding 키)와 섞이지 않도록
            # 캐시를 거치지 않고 항상 새로 빌드한다.
            override_model = self._provider_factory.get_chat_model(model_name=resolved)
            return build_agentic_graph(
                chat_model=override_model,
                tools=lc_tools,
                system_prompt=plan.system_prompt,
                cache_padding_text=plan.cache_padding_text,
            )

        # --- 기존 경로 (폴백 / 회귀 없음) ---
        if context.metadata:
            return build_agentic_graph(
                chat_model=self._chat_model, tools=lc_tools,
                system_prompt=plan.system_prompt,
                cache_padding_text=plan.cache_padding_text,
            )
        return self._get_or_build_agentic_graph(lc_tools, plan)

    async def _execute_agentic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
        trace: Optional[RequestTrace] = None,
        context: Optional[AgentContext] = None,
    ) -> AgentResponse:
        if not self._chat_model:
            logger.warning("agentic_no_chat_model, falling back to deterministic")
            return await self._execute_deterministic(question, plan, session_id, trace=trace, context=context)

        context = context or AgentContext(session_id=session_id)
        tool_instances = [
            inst for group in plan.tool_groups
            for tc in group
            if (inst := self._registry.get(tc.tool_name))
        ]
        lc_tools = convert_tools_to_langchain(tool_instances, context, plan.scope)

        if not lc_tools:
            logger.warning("agentic_no_tools, falling back to deterministic")
            return await self._execute_deterministic(question, plan, session_id, trace=trace, context=context)

        agent_app = self._effective_agentic_app(lc_tools, plan, context)

        effective_question = _build_agentic_user_turn(question, plan)

        result = await agent_app.ainvoke(
            {"messages": [{"role": "user", "content": effective_question}]},
        )

        # 결과 추출
        answer = ""
        tools_called = []
        if "messages" in result:
            for msg in result["messages"]:
                if hasattr(msg, "type") and msg.type == "tool":
                    tools_called.append(msg.name if hasattr(msg, "name") else "unknown")

            # 마지막 AI 메시지가 최종 답변
            last_msg = result["messages"][-1]
            if hasattr(last_msg, "content"):
                answer = _content_to_text(last_msg.content)

        # Guardrail
        guardrail_score: Optional[float] = None
        if plan.guardrail_chain and answer:
            guardrail_ctx = GuardrailContext(
                question=question,
                source_documents=[],
                profile_id=session_id,
                response_policy=plan.response_policy,
            )
            answer, results = await run_guardrail_chain(
                answer, plan.guardrail_chain, self._guardrails, guardrail_ctx,
            )
            guardrail_score = _extract_faithfulness_score(results)

        return AgentResponse(
            answer=answer,
            sources=[],
            trace=TraceInfo(
                question_type=plan.question_type.value,
                mode="agentic",
                tools_called=tools_called,
            ),
            guardrail_score=guardrail_score,
        )

    async def _stream_agentic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
        trace: Optional[RequestTrace] = None,
        context: Optional[AgentContext] = None,
    ) -> AsyncIterator[dict]:
        """에이전틱 모드 스트리밍.

        astream_events로 도구 호출 추적 + 최종 답변 토큰 스트리밍.
        """
        if not self._chat_model:
            async for event in self._stream_deterministic(question, plan, session_id, trace=trace, context=context):
                yield event
            return

        context = context or AgentContext(session_id=session_id)
        tool_instances = [
            inst for group in plan.tool_groups
            for tc in group
            if (inst := self._registry.get(tc.tool_name))
        ]
        lc_tools = convert_tools_to_langchain(tool_instances, context, plan.scope)

        if not lc_tools:
            async for event in self._stream_deterministic(question, plan, session_id, trace=trace, context=context):
                yield event
            return

        agent_app = self._effective_agentic_app(lc_tools, plan, context)

        effective_question = _build_agentic_user_turn(question, plan)

        yield {"type": "trace", "data": {"step": "agentic_start", "mode": "agentic"}}

        tools_called = []
        answer = ""

        # usage 집계 (input/output/cache_read_input_tokens)
        _usage_input: int = 0
        _usage_output: int = 0
        _usage_cache_read: int = 0
        _usage_cache_creation: int = 0

        async for event in agent_app.astream_events(
            {"messages": [{"role": "user", "content": effective_question}]},
            version="v2",
        ):
            kind = event.get("event", "")

            if kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                yield {"type": "trace", "data": {
                    "step": "tool_call",
                    "tool": tool_name,
                }}

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                tools_called.append(tool_name)
                yield {"type": "trace", "data": {
                    "step": "tool_complete",
                    "tool": tool_name,
                }}

            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    if not (hasattr(chunk, "tool_calls") and chunk.tool_calls):
                        text = _content_to_text(chunk.content)
                        if text:
                            yield {"type": "token", "data": text}
                            answer += text
                # 스트리밍 청크에서 usage_metadata 집계 (LangChain usage_metadata)
                if chunk:
                    um = getattr(chunk, "usage_metadata", None)
                    if um:
                        _usage_input += um.get("input_tokens", 0) if isinstance(um, dict) else getattr(um, "input_tokens", 0) or 0
                        _usage_output += um.get("output_tokens", 0) if isinstance(um, dict) else getattr(um, "output_tokens", 0) or 0
                        _usage_cache_read += um.get("input_token_details", {}).get("cache_read", 0) if isinstance(um, dict) else getattr(getattr(um, "input_token_details", None) or {}, "get", lambda k, d=0: d)("cache_read", 0)

            elif kind == "on_chat_model_end":
                # on_chat_model_end: response_metadata 또는 usage_metadata 확인
                data = event.get("data", {})
                output = data.get("output")
                if output:
                    # LangChain AIMessage response_metadata (Anthropic: usage)
                    rm = getattr(output, "response_metadata", None)
                    if rm and isinstance(rm, dict):
                        usage_rm = rm.get("usage", {}) or {}
                        _usage_input = max(_usage_input, usage_rm.get("input_tokens", 0) or 0)
                        _usage_output = max(_usage_output, usage_rm.get("output_tokens", 0) or 0)
                        _usage_cache_read = max(_usage_cache_read, usage_rm.get("cache_read_input_tokens", 0) or 0)
                        _usage_cache_creation = max(_usage_cache_creation, usage_rm.get("cache_creation_input_tokens", 0) or 0)
                    # LangChain usage_metadata (표준 필드)
                    um = getattr(output, "usage_metadata", None)
                    if um:
                        _in = um.get("input_tokens", 0) if isinstance(um, dict) else getattr(um, "input_tokens", 0) or 0
                        _out = um.get("output_tokens", 0) if isinstance(um, dict) else getattr(um, "output_tokens", 0) or 0
                        _usage_input = max(_usage_input, _in)
                        _usage_output = max(_usage_output, _out)

        # Guardrail
        faithfulness_score: Optional[float] = None
        if plan.guardrail_chain and answer:
            guardrail_ctx = GuardrailContext(
                question=question,
                source_documents=[],
                profile_id=session_id,
                response_policy=plan.response_policy,
            )
            modified, results = await run_guardrail_chain(
                answer, plan.guardrail_chain, self._guardrails, guardrail_ctx,
            )
            if modified != answer:
                yield {"type": "trace", "data": {"step": "guardrail_modified", "results": results}}
                yield {"type": "replace", "data": modified}
            faithfulness_score = _extract_faithfulness_score(results)

        done_data: dict = {
            "tools_called": tools_called,
            "sources": [],
        }
        if faithfulness_score is not None:
            done_data["faithfulness_score"] = faithfulness_score

        # usage 집계 결과 → done 봉투 최상위 필드 (task-205 인터페이스)
        # 값이 있거나 0이어도 포함 (backend가 graceful 처리)
        # 단, 모두 0이고 집계된 정보가 없을 때는 생략하지 않고 0값으로 포함
        # (SSE 수신부가 존재 여부만 보는 경우 대비)
        done_data["usage"] = {
            "input_tokens": _usage_input,
            "output_tokens": _usage_output,
            "cache_read_input_tokens": _usage_cache_read,
        }
        if _usage_cache_creation > 0:
            done_data["usage"]["cache_creation_input_tokens"] = _usage_cache_creation

        yield {
            "type": "done",
            "data": done_data,
        }
