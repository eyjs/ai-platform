"""LangGraph 노드 팩토리 함수.

ai-worker 패턴: 팩토리 함수가 의존성을 클로저로 캡처 -> 순수 노드 함수 반환.
"""

import time
from typing import Callable

from src.agent.state import AgentState
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.safety.base import Guardrail, GuardrailContext
from src.tools.base import AgentContext
from src.tools.registry import ToolRegistry

logger = get_logger(__name__)

MAX_CONTENT_PREVIEW_LEN = 500
MAX_SOURCE_PREVIEW_LEN = 200
MAX_SOURCES = 5
GUARDRAIL_BLOCK_TEMPLATE = "답변을 제공할 수 없습니다. 사유: {reason}"


# --- 라우팅 함수 (조건부 엣지) ---


def route_by_rag(state: AgentState) -> str:
    """needs_rag 여부로 다음 노드를 결정한다."""
    if state["plan"].strategy.needs_rag:
        return "execute_tools"
    return "direct_generate"


# --- 노드 팩토리 함수 ---


def create_execute_tools(registry: ToolRegistry) -> Callable:
    """Tool 순차 실행 노드."""

    async def execute_tools(state: AgentState) -> dict:
        plan = state["plan"]
        question = state["question"]
        context = AgentContext(session_id=state["session_id"])
        search_results = []
        tools_called = []
        tool_latencies = []

        for tool in plan.tools:
            tool_name = tool.name if hasattr(tool, "name") else str(tool)
            tools_called.append(tool_name)

            t_start = time.time()
            result = await registry.execute(
                tool_name=tool_name,
                params={"query": question, "subject": question},
                context=context,
                scope=plan.scope,
            )
            tool_ms = (time.time() - t_start) * 1000

            chunks_found = 0
            if result.success and isinstance(result.data, list):
                search_results.extend(result.data)
                chunks_found = len(result.data)

            tool_latencies.append({
                "tool": tool_name,
                "success": result.success,
                "chunks_found": chunks_found,
                "ms": round(tool_ms, 1),
            })
            logger.info(
                "tool_execute",
                tool=tool_name,
                success=result.success,
                chunks_found=chunks_found,
                latency_ms=round(tool_ms, 1),
            )

        return {
            "search_results": search_results,
            "tools_called": tools_called,
            "tool_latencies": tool_latencies,
        }

    return execute_tools


def create_generate_with_context(llm: LLMProvider) -> Callable:
    """검색 결과 기반 LLM 답변 생성 노드."""

    async def generate_with_context(state: AgentState) -> dict:
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
    """Guardrail 체인 실행 노드."""

    async def run_guardrails(state: AgentState) -> dict:
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
        return {"answer": answer, "guardrail_results": results}

    return run_guardrails


def create_build_response() -> Callable:
    """출처 생성 + 최종 응답 조립 노드."""

    async def build_response(state: AgentState) -> dict:
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
            results[name] = {"action": result.action, "ms": round(ms, 1)}

            if result.action == "block":
                logger.warning("guardrail_block", guard=name, reason=result.reason)
                return GUARDRAIL_BLOCK_TEMPLATE.format(reason=result.reason), results
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
        doc_id = r.get("document_id", "")
        if doc_id in seen:
            continue
        seen.add(doc_id)
        sources.append({
            "document_id": doc_id,
            "title": r.get("title", r.get("file_name", "")),
            "chunk_text": r.get("content", "")[:MAX_SOURCE_PREVIEW_LEN],
            "score": r.get("score", 0.0),
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
        return f"질문: {question}\n\n관련 문서를 찾지 못했습니다."

    max_chunks = plan.strategy.max_vector_chunks
    context_parts = []
    for i, r in enumerate(results[:max_chunks], 1):
        title = r.get("title", r.get("file_name", ""))
        content = _format_result(r)
        context_parts.append(f"[{i}] {title}\n{content}")

    context_text = "\n\n".join(context_parts)

    if plan.conversation_context:
        return (
            f"대화 맥락:\n{plan.conversation_context}\n\n"
            f"참고 문서:\n{context_text}\n\n"
            f"질문: {question}"
        )
    return f"참고 문서:\n{context_text}\n\n질문: {question}"
