"""DeterministicExecutorMixin — 결정론적 모드 실행 메서드 모음.

graph_executor.py 분할 산출물. GraphExecutor MRO 상속 경로:
  GraphExecutor(WorkflowExecutorMixin, DeterministicExecutorMixin, AgenticExecutorMixin)
"""

from typing import AsyncIterator, Optional

from src.agent.nodes import build_prompt, build_source_dicts, run_guardrail_chain
from src.agent.state import create_initial_state
from src.agent.executors._helpers import _extract_faithfulness_score
from src.domain.agent_context import AgentContext
from src.domain.execution_plan import ExecutionPlan
from src.domain.models import AgentResponse, SourceRef, TraceInfo
from src.observability.logging import get_logger
from src.observability.trace_logger import RequestTrace
from src.safety.base import GuardrailContext

logger = get_logger(__name__)


class DeterministicExecutorMixin:
    """결정론적 모드(_execute_deterministic / _stream_deterministic)."""

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
