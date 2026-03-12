"""GraphExecutor: 모드별 LangGraph 그래프 선택 + 실행.

UniversalAgent를 대체. execute()/execute_stream() 인터페이스 유지.
"""

import time
from typing import AsyncIterator, Optional

from langchain_core.language_models import BaseChatModel

from src.agent.graphs import build_agentic_graph, build_deterministic_graph
from src.agent.nodes import build_prompt, build_source_dicts, run_guardrail_chain
from src.agent.state import create_initial_state
from src.agent.tool_adapter import convert_tools_to_langchain
from src.domain.models import AgentMode, AgentResponse, SourceRef, TraceInfo
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.observability.trace_logger import RequestTrace
from src.router.execution_plan import ExecutionPlan
from src.safety.base import GuardrailContext
from src.safety.base import Guardrail
from src.tools.base import AgentContext
from src.tools.registry import ToolRegistry

logger = get_logger(__name__)


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
    ):
        self._main_llm = main_llm
        self._registry = tool_registry
        self._guardrails = guardrails or {}
        self._chat_model = chat_model

        # 결정론적 그래프 (한 번 컴파일, 재사용)
        det_graph = build_deterministic_graph(
            llm=main_llm,
            registry=tool_registry,
            guardrails=self._guardrails,
        )
        self._deterministic_app = det_graph.compile()

    async def execute(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str = "",
        trace: Optional[RequestTrace] = None,
    ) -> AgentResponse:
        """ExecutionPlan 기반 실행."""
        start_time = time.time()

        if plan.mode == AgentMode.AGENTIC:
            response = await self._execute_agentic(question, plan, session_id)
        else:
            response = await self._execute_deterministic(question, plan, session_id)

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
    ) -> AsyncIterator[dict]:
        """SSE 스트리밍 실행."""
        start_time = time.time()

        if plan.mode == AgentMode.AGENTIC:
            async for event in self._stream_agentic(question, plan, session_id):
                yield event
        else:
            async for event in self._stream_deterministic(question, plan, session_id):
                yield event

        if trace:
            total_ms = (time.time() - start_time) * 1000
            trace.add_node("graph_stream", duration_ms=round(total_ms, 1))

    # --- 결정론적 모드 ---

    async def _execute_deterministic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
    ) -> AgentResponse:
        initial_state = create_initial_state(question, plan, session_id)
        result = await self._deterministic_app.ainvoke(initial_state)

        tools_called = result.get("tools_called", [])
        sources = [SourceRef(**s) for s in result.get("sources", [])]

        return AgentResponse(
            answer=result.get("answer", ""),
            sources=sources,
            trace=TraceInfo(
                question_type=plan.question_type.value,
                mode=plan.mode.value,
                tools_called=tools_called,
            ),
        )

    async def _stream_deterministic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """결정론적 모드 스트리밍.

        도구 실행은 LangGraph astream으로 노드별 추적,
        LLM 답변은 기존 LLMProvider.generate_stream으로 토큰 스트리밍.
        """
        # RAG 불필요 -> 직접 스트리밍
        if not plan.strategy.needs_rag:
            async for token in self._main_llm.generate_stream(
                question, system=plan.system_prompt,
            ):
                yield {"type": "token", "data": token}
            yield {"type": "done", "data": {"tools_called": [], "sources": []}}
            return

        # Tool 실행 (astream으로 노드별 추적)
        yield {"type": "trace", "data": {"step": "tool_execution", "status": "start"}}

        initial_state = create_initial_state(question, plan, session_id)
        tools_called = []
        search_results = []

        async for chunk in self._deterministic_app.astream(
            initial_state, stream_mode="updates",
        ):
            for node_name, state_update in chunk.items():
                if node_name == "execute_tools":
                    tools_called = state_update.get("tools_called", [])
                    search_results = state_update.get("search_results", [])
                    for tl in state_update.get("tool_latencies", []):
                        yield {"type": "trace", "data": {
                            "tool": tl["tool"],
                            "success": tl["success"],
                            "ms": tl["ms"],
                        }}

        # 답변 토큰 스트리밍
        prompt_results = search_results[:plan.strategy.max_vector_chunks]
        prompt = build_prompt(question, plan, prompt_results)

        yield {"type": "trace", "data": {
            "step": "generation", "status": "start",
            "context_chunks": len(prompt_results),
        }}

        answer_tokens = []
        async for token in self._main_llm.generate_stream(
            prompt, system=plan.system_prompt,
        ):
            answer_tokens.append(token)
            yield {"type": "token", "data": token}

        # Guardrail
        full_answer = "".join(answer_tokens)
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

        sources = build_source_dicts(search_results)
        yield {
            "type": "done",
            "data": {
                "tools_called": tools_called,
                "sources": sources,
            },
        }

    # --- 에이전틱 모드 ---

    async def _execute_agentic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
    ) -> AgentResponse:
        if not self._chat_model:
            logger.warning("agentic_no_chat_model, falling back to deterministic")
            return await self._execute_deterministic(question, plan, session_id)

        context = AgentContext(session_id=session_id)
        lc_tools = convert_tools_to_langchain(plan.tools, context, plan.scope)

        if not lc_tools:
            logger.warning("agentic_no_tools, falling back to deterministic")
            return await self._execute_deterministic(question, plan, session_id)

        agent_app = build_agentic_graph(
            chat_model=self._chat_model,
            tools=lc_tools,
            system_prompt=plan.system_prompt,
        )

        result = await agent_app.ainvoke(
            {"messages": [{"role": "user", "content": question}]},
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
                answer = last_msg.content or ""

        # Guardrail
        if plan.guardrail_chain and answer:
            guardrail_ctx = GuardrailContext(
                question=question,
                source_documents=[],
                profile_id=session_id,
                response_policy=plan.response_policy,
            )
            answer, _ = await run_guardrail_chain(
                answer, plan.guardrail_chain, self._guardrails, guardrail_ctx,
            )

        return AgentResponse(
            answer=answer,
            sources=[],
            trace=TraceInfo(
                question_type=plan.question_type.value,
                mode="agentic",
                tools_called=tools_called,
            ),
        )

    async def _stream_agentic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """에이전틱 모드 스트리밍.

        astream_events로 도구 호출 추적 + 최종 답변 토큰 스트리밍.
        """
        if not self._chat_model:
            async for event in self._stream_deterministic(question, plan, session_id):
                yield event
            return

        context = AgentContext(session_id=session_id)
        lc_tools = convert_tools_to_langchain(plan.tools, context, plan.scope)

        if not lc_tools:
            async for event in self._stream_deterministic(question, plan, session_id):
                yield event
            return

        agent_app = build_agentic_graph(
            chat_model=self._chat_model,
            tools=lc_tools,
            system_prompt=plan.system_prompt,
        )

        yield {"type": "trace", "data": {"step": "agentic_start", "mode": "agentic"}}

        tools_called = []
        answer = ""

        async for event in agent_app.astream_events(
            {"messages": [{"role": "user", "content": question}]},
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
                        yield {"type": "token", "data": chunk.content}
                        answer += chunk.content

        # Guardrail
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

        yield {
            "type": "done",
            "data": {
                "tools_called": tools_called,
                "sources": [],
            },
        }
