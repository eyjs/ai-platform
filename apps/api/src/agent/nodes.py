"""LangGraph 노드 팩토리 함수.

ai-worker 패턴: 팩토리 함수가 의존성을 클로저로 캡처 -> 순수 노드 함수 반환.
"""

import asyncio
import time
from typing import Callable

from collections import defaultdict

from src.agent.state import AgentState
from src.config import settings
from src.infrastructure.providers.base import LLMProvider
from src.locale.bundle import get_locale
from src.observability.logging import get_logger
from src.router.execution_plan import ToolCall
from src.safety.base import Guardrail, GuardrailContext
from src.domain.agent_context import AgentContext
from src.tools.registry import ToolRegistry

logger = get_logger(__name__)

MAX_CONTENT_PREVIEW_LEN = 1500
MAX_SOURCE_PREVIEW_LEN = 200
MAX_SOURCES = 5
MIN_SOURCE_SCORE = 0.3


# --- 헬퍼 함수 ---


def _steps_to_tool_groups(steps: list[dict]) -> list[list[ToolCall]]:
    """Planner가 생성한 steps를 tool_groups 형식으로 변환한다.

    같은 group 번호의 step은 같은 리스트에 넣어 병렬 실행.
    group 번호 순으로 정렬하여 순차 실행 순서 보장.
    """
    groups: dict[int, list[ToolCall]] = defaultdict(list)
    for step in steps:
        group_num = step.get("group", 1)
        groups[group_num].append(
            ToolCall(tool_name=step["tool"], params=step.get("params", {})),
        )
    return [groups[k] for k in sorted(groups.keys())]


# --- 라우팅 함수 (조건부 엣지) ---


def route_by_rag(state: AgentState) -> str:
    """needs_rag 여부로 다음 노드를 결정한다."""
    if state["plan"].strategy.needs_rag:
        return "plan_execution"
    return "direct_generate"


def route_by_evaluation(state: AgentState) -> str:
    """검색 결과 품질에 따라 다음 노드를 결정한다.

    - 결과 충분 (score >= 0.4): generate_with_context로 진행
    - 결과 불충분 & retry 가능: rewrite_query로 재시도
    - 결과 불충분 & retry 소진: generate_with_context로 진행 (best-effort)
    """
    results = state.get("search_results", [])
    retry_count = state.get("retry_count", 0)

    # 결과가 있고 최소 품질 기준 충족
    if results and max(r.get("score", 0) for r in results) >= 0.4:
        return "generate_with_context"

    # 최대 재시도 도달
    if retry_count >= settings.planner_max_retries:
        return "generate_with_context"

    # 재시도 필요 (evaluate_results에서 retry_count가 이미 증가된 상태)
    if retry_count > 0:
        return "rewrite_query"

    return "generate_with_context"


def route_by_guardrail(state: AgentState) -> str:
    """Guardrail 결과에 따라 재생성 여부를 결정한다."""
    gr = state.get("guardrail_results", {})
    if not gr:
        return "build_response"

    regenerate_needed = gr.get("_regenerate_needed", False)
    # retry_count를 재사용하되, guardrail 재생성은 1회만 허용
    # _guardrail_regen_count로 별도 추적
    regen_count = gr.get("_regen_count", 0)

    if regenerate_needed and regen_count < 1:
        return "regenerate"
    return "build_response"


# --- 노드 팩토리 함수 ---


def create_execute_tools(registry: ToolRegistry) -> Callable:
    """Tool 병렬 실행 노드. tool_groups별 asyncio.gather."""

    async def _execute_single(tool_call, context, scope, trace):
        """단일 Tool 실행 + trace 기록."""
        trace_node = trace.start_node(f"tool:{tool_call.tool_name}") if trace else None
        try:
            result = await registry.execute(
                tool_name=tool_call.tool_name,
                params=tool_call.params,
                context=context,
                scope=scope,
            )
            if trace_node:
                trace_node.finish(
                    success=result.success,
                    chunks=len(result.data) if result.data else 0,
                )
            return tool_call, result, trace_node
        except Exception as e:
            if trace_node:
                trace_node.finish(success=False, error=str(e))
            raise

    async def execute_tools(state: AgentState) -> dict:
        plan = state["plan"]
        context = AgentContext(session_id=state["session_id"])
        trace = state.get("trace")
        search_results = []
        tools_called = []
        tool_latencies = []

        # Step-aware 실행: Planner가 생성한 steps가 있으면 사용, 없으면 기존 tool_groups 폴백
        planned_steps = state.get("planned_steps", [])
        if planned_steps:
            tool_groups = _steps_to_tool_groups(planned_steps)
        else:
            tool_groups = plan.tool_groups

        for group in tool_groups:
            tasks = [
                _execute_single(tc, context, plan.scope, trace)
                for tc in group
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for tc, outcome in zip(group, results):
                if isinstance(outcome, Exception):
                    logger.warning("tool_failed", tool=tc.tool_name, error=str(outcome))
                    tool_latencies.append({
                        "tool": tc.tool_name, "success": False,
                        "chunks_found": 0, "ms": 0,
                    })
                    continue

                _, result, trace_node = outcome
                tools_called.append(tc.tool_name)
                chunks_found = len(result.data) if result.success and result.data else 0
                if result.success and result.data:
                    search_results.extend(result.data)

                node_ms = trace_node.duration_ms if trace_node else 0
                tool_latencies.append({
                    "tool": tc.tool_name, "success": result.success,
                    "chunks_found": chunks_found, "ms": round(node_ms, 1),
                })
                logger.info(
                    "tool_execute", tool=tc.tool_name,
                    success=result.success, chunks_found=chunks_found,
                    latency_ms=round(node_ms, 1),
                )

        return {
            "search_results": search_results,
            "tools_called": tools_called,
            "tool_latencies": tool_latencies,
        }

    return execute_tools


def create_evaluate_results() -> Callable:
    """검색 결과 품질 평가 노드.

    score >= 0.4인 결과가 있으면 충분, 없으면 retry_count를 증가시켜
    route_by_evaluation에서 재시도 경로를 선택하도록 한다.
    """

    async def evaluate_results(state: AgentState) -> dict:
        results = state.get("search_results", [])
        retry_count = state.get("retry_count", 0)

        # 결과가 충분하면 그대로 진행
        if results and max(r.get("score", 0) for r in results) >= 0.4:
            return {"retry_count": retry_count}

        # 최대 재시도 도달
        if retry_count >= settings.planner_max_retries:
            return {"retry_count": retry_count}

        # 재시도 필요: retry_count 증가
        return {"retry_count": retry_count + 1}

    return evaluate_results


def create_rewrite_query(llm: LLMProvider) -> Callable:
    """쿼리 재작성 노드.

    검색 결과가 불충분할 때 LLM에게 쿼리를 재작성 요청하고,
    새로운 planned_steps를 반환하여 execute_tools가 재실행되도록 한다.
    """

    async def rewrite_query(state: AgentState) -> dict:
        question = state["question"]
        results = state.get("search_results", [])

        max_score = max((r.get("score", 0) for r in results), default=0)
        prompt = (
            f"원래 질문에 대한 검색 결과가 불충분합니다.\n"
            f"원래 질문: {question}\n"
            f"검색 결과 수: {len(results)}\n"
            f"최고 유사도: {max_score:.2f}\n\n"
            f"검색 쿼리를 재작성하세요. 더 일반적이거나 다른 표현을 사용하세요.\n"
            f'JSON 형식: {{"steps": [{{"step_id": "retry", "tool": "rag_search", '
            f'"params": {{"query": "재작성된 쿼리"}}, "group": 1}}], '
            f'"reasoning": "재작성 이유"}}'
        )

        try:
            result = await asyncio.wait_for(
                llm.generate_json(prompt),
                timeout=settings.planner_timeout,
            )
            new_steps = result.get("steps", [])
            reasoning = result.get("reasoning", "")
            if new_steps:
                logger.info("rewrite_query_success", reasoning=reasoning[:100])
                return {
                    "planned_steps": new_steps,
                    "planning_reasoning": f"retry: {reasoning}",
                }
        except Exception as e:
            logger.warning("rewrite_query_failed", error=str(e))

        # 실패 시 원래 질문으로 rag_search 재실행
        return {
            "planned_steps": [
                {"step_id": "retry_fallback", "tool": "rag_search",
                 "params": {"query": question}, "group": 1},
            ],
            "planning_reasoning": "rewrite failed, retrying with original query",
        }

    return rewrite_query


def create_regenerate(llm: LLMProvider) -> Callable:
    """Guardrail 실패 시 답변 재생성 노드.

    이전 답변의 guardrail 실패 이유를 system_prompt에 추가하여
    LLM에게 개선된 답변을 요청한다. 최대 1회만 실행.
    """

    async def regenerate(state: AgentState) -> dict:
        plan = state["plan"]
        question = state["question"]
        results = state.get("search_results", [])
        gr = state.get("guardrail_results", {})

        # guardrail 실패 이유 수집
        warnings = []
        for name, v in gr.items():
            if name.startswith("_"):
                continue
            if isinstance(v, dict) and v.get("action") == "warn":
                warnings.append(f"{name}: score={v.get('score')}")

        warning_text = ", ".join(warnings) if warnings else "low quality"

        # 프롬프트에 guardrail 피드백 추가
        max_chunks = plan.strategy.max_vector_chunks
        prompt_results = results[:max_chunks]
        prompt = build_prompt(question, plan, prompt_results)

        enhanced_system = (
            f"{plan.system_prompt}\n\n"
            f"[IMPORTANT] 이전 답변이 품질 검증에서 부족한 평가를 받았습니다 "
            f"({warning_text}). "
            f"참고 문서에 충실하게 답변하세요. 근거 없는 내용은 포함하지 마세요."
        )

        answer = await llm.generate(prompt, system=enhanced_system)
        logger.info("regenerate_answer", answer_len=len(answer), warnings=warning_text)

        # _regen_count 증가하여 재생성 루프 방지
        updated_gr = {**gr, "_regenerate_needed": False, "_regen_count": gr.get("_regen_count", 0) + 1}
        return {"answer": answer, "guardrail_results": updated_gr}

    return regenerate


def create_generate_with_context(llm: LLMProvider) -> Callable:
    """검색 결과 기반 LLM 답변 생성 노드.

    is_streaming=True이면 바이패스 (래퍼에서 토큰 스트리밍 직접 처리).
    """

    async def generate_with_context(state: AgentState) -> dict:
        if state.get("is_streaming"):
            return {}

        plan = state["plan"]
        question = state["question"]
        results = state["search_results"]

        max_chunks = plan.strategy.max_vector_chunks
        prompt_results = results[:max_chunks]

        prompt = build_prompt(question, plan, prompt_results)
        answer = await llm.generate(prompt, system=plan.system_prompt)

        logger.info("llm_generate", answer_len=len(answer), context_chunks=len(prompt_results))
        return {"answer": answer}

    return generate_with_context


def create_direct_generate(llm: LLMProvider) -> Callable:
    """직접 답변 생성 노드 (RAG 불필요)."""

    async def direct_generate(state: AgentState) -> dict:
        question = state["question"]
        plan = state["plan"]
        answer = await llm.generate(question, system=plan.system_prompt)
        logger.info("direct_generate", answer_len=len(answer))
        return {"answer": answer}

    return direct_generate


def create_run_guardrails(guardrails: dict[str, Guardrail]) -> Callable:
    """Guardrail 체인 실행 노드.

    is_streaming=True이면 바이패스 (래퍼에서 스트리밍 후 직접 실행).
    """

    async def run_guardrails(state: AgentState) -> dict:
        if state.get("is_streaming"):
            return {}

        plan = state["plan"]
        answer = state["answer"]

        if not plan.guardrail_chain:
            return {"guardrail_results": {}}

        context = GuardrailContext(
            question=state["question"],
            source_documents=state["search_results"],
            profile_id=state["session_id"],
            response_policy=plan.response_policy,
        )

        answer, results = await run_guardrail_chain(
            answer, plan.guardrail_chain, guardrails, context,
        )

        # Guardrail 재생성 판단: warn + score < 0.5이면 재생성 후보
        regenerate_needed = any(
            isinstance(v, dict) and v.get("action") == "warn"
            and v.get("score") is not None and v.get("score") < 0.5
            for v in results.values()
        )
        results["_regenerate_needed"] = regenerate_needed
        results["_regen_count"] = state.get("guardrail_results", {}).get("_regen_count", 0)

        return {"answer": answer, "guardrail_results": results}

    return run_guardrails


def create_build_response() -> Callable:
    """출처 생성 + 최종 응답 조립 노드.

    is_streaming=True이면 바이패스 (래퍼에서 done 이벤트로 직접 전달).
    """

    async def build_response(state: AgentState) -> dict:
        if state.get("is_streaming"):
            return {}

        sources = build_source_dicts(state["search_results"])
        return {"sources": sources}

    return build_response


# --- 공용 헬퍼 (노드 + GraphExecutor 양쪽에서 사용) ---


async def run_guardrail_chain(
    answer: str,
    guardrail_names: list[str],
    guardrails: dict[str, Guardrail],
    context: GuardrailContext,
) -> tuple[str, dict]:
    """Guardrail 체인을 순차 실행하고 (최종 answer, 결과 dict)를 반환한다."""
    results = {}
    for name in guardrail_names:
        guardrail = guardrails.get(name)
        if not guardrail:
            results[name] = "skipped"
            continue
        try:
            t = time.time()
            result = await guardrail.check(answer, context)
            ms = (time.time() - t) * 1000
            # Task 014: guardrail 스코어를 결과에 포함 (None 가능)
            results[name] = {
                "action": result.action,
                "ms": round(ms, 1),
                "score": result.score,
            }

            if result.action == "block":
                logger.warning("guardrail_block", guard=name, reason=result.reason)
                return get_locale().message("guardrail_block", reason=result.reason), results
            if result.action == "warn" and result.modified_answer:
                logger.info("guardrail_warn", guard=name, reason=result.reason)
                answer = result.modified_answer
        except Exception as e:
            logger.warning("guardrail_error", guard=name, error=str(e))
            results[name] = {"action": "error", "error": str(e)}
    return answer, results


def build_source_dicts(results: list[dict]) -> list[dict]:
    """검색 결과를 중복 제거된 출처 dict 리스트로 변환한다."""
    sources = []
    seen: set[str] = set()
    for r in results:
        score = r.get("score", 0.0)
        if score < MIN_SOURCE_SCORE:
            continue
        doc_id = r.get("document_id", "")
        if doc_id in seen:
            continue
        seen.add(doc_id)
        title = r.get("title") or r.get("file_name") or ""
        sources.append({
            "document_id": doc_id,
            "title": title,
            "file_name": r.get("file_name", ""),
            "chunk_text": r.get("content", "")[:MAX_SOURCE_PREVIEW_LEN],
            "score": score,
            "relevance": score,
            "method": r.get("method", "vector"),
        })
    return sources[:MAX_SOURCES]


# --- 프롬프트 헬퍼 ---


def _format_result(r: dict) -> str:
    """검색 결과를 프롬프트 텍스트로 변환."""
    if "content" in r:
        return r["content"][:MAX_CONTENT_PREVIEW_LEN]
    if "subject" in r and "predicate" in r and "object" in r:
        parts = [f"{r['subject']} — {r['predicate']}: {r['object']}"]
        if r.get("table_context"):
            parts.append(f"(맥락: {r['table_context']})")
        return " ".join(parts)
    return str(r)[:MAX_CONTENT_PREVIEW_LEN]





def build_prompt(question: str, plan, results: list[dict]) -> str:
    """검색 결과를 포함한 LLM 프롬프트 생성."""
    if not results:
        return get_locale().message("no_search_results", question=question)

    max_chunks = plan.strategy.max_vector_chunks
    context_parts = []
    for i, r in enumerate(results[:max_chunks], 1):
        # 프론트에서 file_name으로 소스를 표시하므로 프롬프트도 file_name 사용
        doc_ref = r.get("file_name") or r.get("title", "")
        content = _format_result(r)
        context_parts.append(f"[{i}] {doc_ref}\n{content}")

    context_text = "\n\n".join(context_parts)

    parts = [get_locale().prompt("rag_instruction"), f"\n\n참고 문서:\n{context_text}"]
    if plan.conversation_context:
        parts.append(f"\n\n대화 맥락:\n{plan.conversation_context}")
    parts.append(f"\n\n질문: {question}")

    return "".join(parts)
