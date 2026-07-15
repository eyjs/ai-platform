"""DeterministicExecutorMixin — 결정론적 모드 실행 메서드 모음.

graph_executor.py 분할 산출물. GraphExecutor MRO 상속 경로:
  GraphExecutor(WorkflowExecutorMixin, DeterministicExecutorMixin, AgenticExecutorMixin)
"""

import time
from typing import AsyncIterator, Optional

from src.agent.nodes import build_prompt, build_source_dicts, run_guardrail_chain
from src.agent.state import create_initial_state
from src.agent.executors._helpers import (
    _extract_faithfulness_score, _collect_guardrail_warnings,
    REGENERATE_SCORE_THRESHOLD, is_no_answer_dominant, widen_plan,
    insufficient_context_refusal,
)
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

        # 무답변 확장 재시도 (스트리밍 경로와 동일 계약 — _stream_deterministic 참조)
        if plan.strategy.needs_rag and is_no_answer_dominant(result.get("answer", "")):
            widened = widen_plan(plan)
            logger.info(
                "no_answer_widen_retry",
                from_chunks=plan.strategy.max_vector_chunks,
                to_chunks=widened.strategy.max_vector_chunks,
            )
            retry_state = create_initial_state(question, widened, session_id, trace=trace)
            result = await self._deterministic_app.ainvoke(retry_state)

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
        _widen_attempt: int = 0,
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
                question,
                cacheable_system=plan.system_prompt,
                volatile_system=plan.volatile_system_prompt,
                max_tokens=plan.max_output_tokens,
            ):
                if chunk.kind == "thinking":
                    yield {"type": "thinking", "data": chunk.content}
                else:
                    yield {"type": "token", "data": chunk.content}
            yield {"type": "done", "data": {"tools_called": [], "sources": []}}
            return

        # Tool 실행만 수행 (is_streaming=True → LLM/Guardrail 노드 바이패스)
        t_tools = time.time()
        yield {"type": "trace", "data": {"step": "tool_execution", "status": "start"}}

        initial_state = create_initial_state(
            question, plan, session_id, is_streaming=True, trace=trace,
        )
        tools_called = []
        search_results = []
        retry_count = 0

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
                        tool_event: dict = {
                            "tool": tl["tool"],
                            "success": tl["success"],
                            "ms": tl["ms"],
                            "chunks_found": tl.get("chunks_found", 0),
                        }
                        # 청크 상세(필터·후보·리랭킹) 실시간 동봉
                        if tl.get("detail"):
                            tool_event["detail"] = tl["detail"]
                        yield {"type": "trace", "data": tool_event}
                elif node_name == "graph_enrich":
                    if "search_results" in state_update:
                        search_results = state_update["search_results"]
                    enrichment = state_update.get("graph_enrichment", {})
                    if enrichment.get("enriched") or enrichment.get("discovered"):
                        # 온톨로지 탐색 상세(엣지·발견 문서·필터 사유)를 그대로 동봉 —
                        # 채팅 트레이스 패널이 "그래프가 무엇을 왜 살렸나"를 렌더한다
                        yield {"type": "trace", "data": {
                            "step": "graph_enrich",
                            "enriched": enrichment.get("enriched", 0),
                            "discovered": enrichment.get("discovered", 0),
                            "detail": {
                                "seeds": enrichment.get("seeds", []),
                                "edges": enrichment.get("edges", []),
                                "discovered": enrichment.get("discovered_docs", []),
                                "enriched": enrichment.get("enriched_docs", []),
                                "skipped": enrichment.get("skipped", {}),
                            },
                        }}
                elif node_name == "evaluate_results":
                    # 검색 결과 충분성 판정 (Adaptive Retry 분기점) 실시간 노출.
                    # 충분하면 retry_count 유지, 불충분하면 +1 후 rewrite_query 로 분기.
                    yield {"type": "trace", "data": {
                        "step": "evaluate_results",
                        "retry_count": state_update.get("retry_count"),
                    }}
                elif node_name == "rewrite_query":
                    # 재시도 루프 발화 — 재검색 왕복이 여기서 배증한다
                    retry_count += 1
                    logger.info("adaptive_retry", attempt=retry_count)
                    yield {"type": "trace", "data": {
                        "step": "rewrite_query", "status": "retry", "attempt": retry_count,
                    }}

        tools_ms = (time.time() - t_tools) * 1000
        logger.info(
            "retrieval_complete",
            ms=round(tools_ms, 1), tools=tools_called,
            results=len(search_results), retries=retry_count,
        )
        yield {"type": "trace", "data": {
            "step": "tool_execution", "status": "end",
            "ms": round(tools_ms, 1),
            "results": len(search_results),
            "retries": retry_count,
        }}

        # LLM 토큰 스트리밍 (래퍼에서 직접 처리)
        # graph_enrich 결과가 뒤에 추가되므로 score 기준 정렬 후 슬라이스
        search_results.sort(key=lambda r: r.get("score", 0), reverse=True)
        prompt_results = search_results[:plan.strategy.max_vector_chunks]
        prompt = build_prompt(question, plan, prompt_results)

        # 관련도 게이트: needs_rag인데 관련 컨텍스트가 하나도 없으면(무관 검색이
        # 리랭커 하한 미달로 빈 결과) LLM을 태우지 않고 정직 반려를 결정론적으로
        # 방출한다 — 무관 청크로 지어내는 것을 원천 차단(환각 방지).
        refusal = insufficient_context_refusal(plan, prompt_results)

        yield {"type": "trace", "data": {
            "step": "generation", "status": "start",
            "context_chunks": len(prompt_results),
            "refused": refusal is not None,
        }}

        answer_tokens = []
        # 생성 계측: ttft(prefill 체감) + tok/s(decode) — 지금까지 graph_stream 총합에
        # 흡수돼 안 보이던 지배적 지연을 실시간 노출한다.
        gen_start = time.time()
        ttft_ms: Optional[float] = None
        thinking_chunks = 0
        answer_chunk_count = 0
        if refusal is not None:
            logger.info(
                "insufficient_context_refusal",
                context_chunks=len(prompt_results),
                needs_rag=plan.strategy.needs_rag,
            )
            ttft_ms = 0.0
            answer_chunk_count = 1
            answer_tokens.append(refusal)
            yield {"type": "token", "data": refusal}
        else:
            # thinking/answer 분리 스트리밍 (base 기본 구현은 전부 answer)
            # volatile(날짜·간결 지시 등 per-turn)도 전달 — 기존엔 스트리밍 경로에서 유실됐다.
            async for chunk in self._main_llm.generate_stream_typed(
                prompt,
                cacheable_system=plan.system_prompt,
                volatile_system=plan.volatile_system_prompt,
                max_tokens=plan.max_output_tokens,
            ):
                if ttft_ms is None:
                    ttft_ms = (time.time() - gen_start) * 1000
                if chunk.kind == "thinking":
                    thinking_chunks += 1
                    yield {"type": "thinking", "data": chunk.content}
                else:
                    answer_chunk_count += 1
                    answer_tokens.append(chunk.content)
                    yield {"type": "token", "data": chunk.content}

        gen_ms = (time.time() - gen_start) * 1000
        total_chunks = thinking_chunks + answer_chunk_count
        ttft = round(ttft_ms or 0.0, 1)
        decode_ms = max(gen_ms - ttft, 1e-6)
        chunks_per_s = round(total_chunks / (decode_ms / 1000), 1) if total_chunks else 0.0
        logger.info(
            "generation_complete",
            ms=round(gen_ms, 1), ttft_ms=ttft, chunks=total_chunks,
            thinking_chunks=thinking_chunks, chunks_per_s=chunks_per_s,
            context_chunks=len(prompt_results),
        )
        if trace:
            # 스트리밍에서 바이패스되는 generate 노드를 수동으로 최종 트레이스에 기록
            trace.add_node(
                "generate_with_context", duration_ms=gen_ms,
                ttft_ms=ttft, chunks_per_s=chunks_per_s, chunks=total_chunks,
            )
        yield {"type": "trace", "data": {
            "step": "generation", "status": "end",
            "ms": round(gen_ms, 1),
            "ttft_ms": ttft,
            "chunks_per_s": chunks_per_s,
            "chunks": total_chunks,
            "thinking_chunks": thinking_chunks,
        }}

        # 무답변 확장 재시도 — 답변이 "정보 부재" 정형 문구면 검색 정원을 2배로
        # 넓혀 전체(검색→생성)를 1회 재실행한다. 경계선 정원 컷으로 정답 청크가
        # 빠진 비결정 오답을 결정론적으로 복구한다(실사고: 실손 가입자격 —
        # 정답 청크 fused 0.646이 정원 5 밖 6위, 실행에 따라 성패 왕복).
        # 넓혀도 무답변이면 그때가 진짜 "없음"(정직 답변 유지).
        _probe_answer = "".join(answer_tokens)
        if (
            _widen_attempt == 0
            and refusal is None  # 결정론 반려(빈 컨텍스트)는 정원 확대로 못 살림 — 재시도 무의미
            and plan.strategy.needs_rag
            and is_no_answer_dominant(_probe_answer)
        ):
            widened = widen_plan(plan)
            logger.info(
                "no_answer_widen_retry",
                from_chunks=plan.strategy.max_vector_chunks,
                to_chunks=widened.strategy.max_vector_chunks,
            )
            yield {"type": "trace", "data": {
                "step": "widen_retry", "status": "start",
                "reason": "no_answer_detected",
                "from_chunks": plan.strategy.max_vector_chunks,
                "to_chunks": widened.strategy.max_vector_chunks,
            }}
            # 이미 스트리밍된 무답변 텍스트를 화면에서 비우고 재시도 결과로 대체
            yield {"type": "replace", "data": ""}
            async for event in self._stream_deterministic(
                question, widened, session_id,
                trace=trace, context=context, _widen_attempt=1,
            ):
                yield event
            return

        # Guardrail (래퍼에서 직접 처리)
        full_answer = "".join(answer_tokens)
        faithfulness_score: Optional[float] = None
        if plan.guardrail_chain:
            gr_start = time.time()
            yield {"type": "trace", "data": {"step": "guardrail", "status": "start"}}
            guardrail_ctx = GuardrailContext(
                question=question,
                source_documents=search_results,
                profile_id=session_id,
                response_policy=plan.response_policy,
            )
            modified, results = await run_guardrail_chain(
                full_answer, plan.guardrail_chain, self._guardrails, guardrail_ctx,
            )
            # Task 014: faithfulness 스코어 포집 → done 이벤트 동봉
            faithfulness_score = _extract_faithfulness_score(results)

            # 가드레일 판정 기반 재생성 — "내용이 올바른가"의 결정권은 가드레일.
            # 심각 위반(score < 0.35: 연산 왜곡 0.2, deep_eval fail 0.3)이면 같은
            # 컨텍스트로 경고를 주입해 1회 재생성한다(검색은 무죄 — 재검색 없음.
            # 비스트리밍 그래프의 regenerate 노드와 동일 계약을 스트림에 이식).
            if (
                faithfulness_score is not None
                and faithfulness_score < REGENERATE_SCORE_THRESHOLD
                and _widen_attempt == 0  # 확장 재시도와 합산 폭주 방지(총 2패스 상한)
            ):
                warning_text = _collect_guardrail_warnings(results)
                logger.info(
                    "guardrail_regenerate",
                    score=faithfulness_score, warnings=warning_text,
                )
                yield {"type": "trace", "data": {
                    "step": "regenerate", "status": "start",
                    "reason": warning_text, "score": faithfulness_score,
                }}
                yield {"type": "replace", "data": ""}
                feedback = (
                    f"[IMPORTANT] 이전 답변이 품질 검증에서 심각한 지적을 받았습니다: "
                    f"{warning_text}. 참고 문서의 계산 조항은 원문 연산 그대로 "
                    f"인용하고, 근거 없는 내용은 포함하지 마세요."
                )
                volatile_extra = (
                    f"{plan.volatile_system_prompt}\n\n{feedback}"
                    if plan.volatile_system_prompt else feedback
                )
                answer_tokens = []
                async for chunk in self._main_llm.generate_stream_typed(
                    prompt,
                    cacheable_system=plan.system_prompt,
                    volatile_system=volatile_extra,
                    max_tokens=plan.max_output_tokens,
                ):
                    if chunk.kind == "thinking":
                        yield {"type": "thinking", "data": chunk.content}
                    else:
                        answer_tokens.append(chunk.content)
                        yield {"type": "token", "data": chunk.content}
                full_answer = "".join(answer_tokens)
                modified, results = await run_guardrail_chain(
                    full_answer, plan.guardrail_chain, self._guardrails, guardrail_ctx,
                )
                faithfulness_score = _extract_faithfulness_score(results)
                yield {"type": "trace", "data": {
                    "step": "regenerate", "status": "end",
                    "score": faithfulness_score,
                }}

            if modified != full_answer:
                yield {"type": "trace", "data": {"step": "guardrail_modified", "results": results}}
                yield {"type": "replace", "data": modified}
            gr_ms = (time.time() - gr_start) * 1000
            logger.info("guardrail_complete", ms=round(gr_ms, 1), modified=(modified != full_answer))
            if trace:
                trace.add_node("run_guardrails", duration_ms=gr_ms)
            yield {"type": "trace", "data": {
                "step": "guardrail", "status": "end",
                "ms": round(gr_ms, 1),
                "modified": modified != full_answer,
            }}

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
