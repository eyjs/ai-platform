"""Universal Agent: 결정론적 RAG 파이프라인 (MVP).

ExecutionPlan을 받아 Tool 실행 -> LLM 답변 생성 -> Guardrail 체인.
"""

import time
from typing import Any, Optional

from src.domain.models import AgentResponse, SourceRef, TraceInfo
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.observability.trace_logger import RequestTrace
from src.router.execution_plan import ExecutionPlan
from src.safety.base import Guardrail, GuardrailContext
from src.tools.base import AgentContext, ToolResult
from src.tools.registry import ToolRegistry

logger = get_logger(__name__)

GUARDRAIL_BLOCK_TEMPLATE = "답변을 제공할 수 없습니다. 사유: {reason}"
MAX_CONTENT_PREVIEW_LEN = 500
MAX_SOURCE_PREVIEW_LEN = 200
MAX_SOURCES = 5


class UniversalAgent:
    """Profile 기반 Universal Agent.

    Agent는 하나, 행동은 ExecutionPlan이 결정한다.
    """

    def __init__(
        self,
        main_llm: LLMProvider,
        tool_registry: ToolRegistry,
        guardrails: Optional[dict[str, Guardrail]] = None,
    ):
        self._main_llm = main_llm
        self._registry = tool_registry
        self._guardrails = guardrails or {}

    async def execute(
        self,
        question: str,
        plan: ExecutionPlan,
        context: AgentContext,
        trace: Optional[RequestTrace] = None,
    ) -> AgentResponse:
        """ExecutionPlan 기반 실행."""
        start_time = time.time()
        tools_called = []
        all_results: list[ToolResult] = []

        # 1. RAG 불필요 (인사, 시스템 질문)
        if not plan.strategy.needs_rag:
            logger.info("direct_response", question_type=plan.question_type.value)
            gen_node = trace.start_node("llm_generate") if trace else None
            answer = await self._generate_direct(question, plan)
            if gen_node:
                gen_node.finish(type="direct", answer_len=len(answer))
            return AgentResponse(
                answer=answer,
                trace=TraceInfo(
                    question_type=plan.question_type.value,
                    mode=plan.mode.value,
                    latency_ms=(time.time() - start_time) * 1000,
                ),
            )

        # 2. Tool 실행 (순서대로)
        for tool in plan.tools:
            tool_name = tool.name if hasattr(tool, "name") else str(tool)
            tools_called.append(tool_name)

            tool_node = trace.start_node(f"tool:{tool_name}") if trace else None
            t_start = time.time()

            result = await self._registry.execute(
                tool_name=tool_name,
                params={"query": question, "subject": question},
                context=context,
                scope=plan.scope,
            )
            all_results.append(result)

            tool_ms = (time.time() - t_start) * 1000
            chunks_found = len(result.data) if result.success and isinstance(result.data, list) else 0
            logger.info(
                "tool_execute",
                tool=tool_name,
                success=result.success,
                chunks_found=chunks_found,
                latency_ms=round(tool_ms, 1),
            )
            if tool_node:
                tool_node.finish(
                    success=result.success,
                    chunks_found=chunks_found,
                    error=result.error if not result.success else None,
                )

        # 3. 검색 결과 수집
        search_results = []
        for result in all_results:
            if result.success and isinstance(result.data, list):
                search_results.extend(result.data)

        logger.info(
            "search_results_collected",
            total_chunks=len(search_results),
            tools_count=len(tools_called),
        )

        # 4. 프롬프트에 사용할 결과 슬라이스 (출처와 동일한 범위)
        max_chunks = plan.strategy.max_vector_chunks
        prompt_results = search_results[:max_chunks]

        # 5. LLM 답변 생성
        gen_node = trace.start_node("llm_generate") if trace else None
        answer = await self._generate_with_context(question, plan, prompt_results)
        if gen_node:
            gen_node.finish(
                type="rag",
                context_chunks=len(prompt_results),
                answer_len=len(answer),
            )
        logger.info("llm_generate", answer_len=len(answer))

        guardrail_context = GuardrailContext(
            question=question,
            source_documents=prompt_results,
            profile_id=context.session_id,
            response_policy=plan.response_policy,
        )
        safety_node = trace.start_node("guardrails") if trace else None
        answer, guardrail_results = await self._run_guardrails(
            answer, plan.guardrail_chain, guardrail_context,
        )
        if safety_node:
            safety_node.finish(checks=guardrail_results)
        logger.info("guardrails_complete", results=guardrail_results)

        # 7. 출처 생성 (프롬프트와 동일한 범위)
        sources = self._build_sources(prompt_results)

        total_ms = (time.time() - start_time) * 1000
        logger.info(
            "agent_complete",
            tools_called=tools_called,
            sources_count=len(sources),
            total_ms=round(total_ms, 1),
        )

        return AgentResponse(
            answer=answer,
            sources=sources,
            trace=TraceInfo(
                question_type=plan.question_type.value,
                mode=plan.mode.value,
                tools_called=tools_called,
                latency_ms=total_ms,
            ),
        )

    async def execute_stream(
        self,
        question: str,
        plan: ExecutionPlan,
        context: AgentContext,
        trace: Optional[RequestTrace] = None,
    ):
        """SSE 스트리밍 실행. token을 yield한다."""
        tools_called = []
        all_results: list[ToolResult] = []

        # RAG 불필요
        if not plan.strategy.needs_rag:
            logger.info("stream_direct_response", question_type=plan.question_type.value)
            async for token in self._main_llm.generate_stream(
                question, system=plan.system_prompt,
            ):
                yield {"type": "token", "data": token}
            yield {"type": "done", "data": {"tools_called": [], "sources": []}}
            return

        # Tool 실행
        yield {"type": "trace", "data": {"step": "tool_execution", "status": "start"}}
        for tool in plan.tools:
            tool_name = tool.name if hasattr(tool, "name") else str(tool)
            tools_called.append(tool_name)

            t_start = time.time()
            result = await self._registry.execute(
                tool_name=tool_name,
                params={"query": question, "subject": question},
                context=context,
                scope=plan.scope,
            )
            all_results.append(result)
            tool_ms = (time.time() - t_start) * 1000

            chunks_found = len(result.data) if result.success and isinstance(result.data, list) else 0
            logger.info(
                "stream_tool_execute",
                tool=tool_name,
                success=result.success,
                chunks_found=chunks_found,
                latency_ms=round(tool_ms, 1),
            )
            if trace:
                trace.add_node(f"tool:{tool_name}", tool_ms, success=result.success, chunks_found=chunks_found)

            yield {"type": "trace", "data": {"tool": tool_name, "success": result.success, "ms": round(tool_ms, 1)}}

        # 검색 결과
        search_results = []
        for result in all_results:
            if result.success and isinstance(result.data, list):
                search_results.extend(result.data)

        # 스트리밍 답변 생성
        prompt = self._build_prompt(question, plan, search_results)
        yield {"type": "trace", "data": {"step": "generation", "status": "start", "context_chunks": len(search_results)}}

        gen_start = time.time()
        answer_tokens = []
        async for token in self._main_llm.generate_stream(
            prompt, system=plan.system_prompt,
        ):
            answer_tokens.append(token)
            yield {"type": "token", "data": token}
        gen_ms = (time.time() - gen_start) * 1000

        if trace:
            trace.add_node("llm_generate", gen_ms, type="rag_stream", context_chunks=len(search_results))
        logger.info("stream_generate_complete", latency_ms=round(gen_ms, 1))

        # Guardrail 체인 (스트리밍 후 전체 답변에 적용)
        full_answer = "".join(answer_tokens)
        if plan.guardrail_chain:
            guardrail_context = GuardrailContext(
                question=question,
                source_documents=search_results,
                profile_id=context.session_id,
                response_policy=plan.response_policy,
            )
            modified_answer, guardrail_results = await self._run_guardrails(
                full_answer, plan.guardrail_chain, guardrail_context,
            )
            if trace:
                trace.add_node("guardrails", 0, checks=guardrail_results)
            if modified_answer != full_answer:
                yield {"type": "trace", "data": {"step": "guardrail_modified", "results": guardrail_results}}
                yield {"type": "replace", "data": modified_answer}

        sources = self._build_sources(search_results)
        yield {
            "type": "done",
            "data": {
                "tools_called": tools_called,
                "sources": [s.model_dump() for s in sources],
            },
        }

    async def _generate_direct(self, question: str, plan: ExecutionPlan) -> str:
        return await self._main_llm.generate(question, system=plan.system_prompt)

    async def _generate_with_context(
        self, question: str, plan: ExecutionPlan, results: list[dict],
    ) -> str:
        prompt = self._build_prompt(question, plan, results)
        return await self._main_llm.generate(prompt, system=plan.system_prompt)

    @staticmethod
    def _format_result(r: dict) -> str:
        """검색 결과를 프롬프트 텍스트로 변환한다. RAG chunk와 fact 양쪽 지원."""
        if "content" in r:
            return r["content"][:MAX_CONTENT_PREVIEW_LEN]
        if "subject" in r and "predicate" in r and "object" in r:
            parts = [f"{r['subject']} — {r['predicate']}: {r['object']}"]
            if r.get("table_context"):
                parts.append(f"(맥락: {r['table_context']})")
            return " ".join(parts)
        return str(r)[:MAX_CONTENT_PREVIEW_LEN]

    @staticmethod
    def _build_prompt(question: str, plan: ExecutionPlan, results: list[dict]) -> str:
        if not results:
            return f"질문: {question}\n\n관련 문서를 찾지 못했습니다."

        max_chunks = plan.strategy.max_vector_chunks
        context_parts = []
        for i, r in enumerate(results[:max_chunks], 1):
            title = r.get("title", r.get("file_name", ""))
            content = UniversalAgent._format_result(r)
            context_parts.append(f"[{i}] {title}\n{content}")

        context_text = "\n\n".join(context_parts)

        if plan.conversation_context:
            return (
                f"대화 맥락:\n{plan.conversation_context}\n\n"
                f"참고 문서:\n{context_text}\n\n"
                f"질문: {question}"
            )
        return f"참고 문서:\n{context_text}\n\n질문: {question}"

    async def _run_guardrails(
        self,
        answer: str,
        guardrail_names: list[str],
        context: GuardrailContext,
    ) -> tuple[str, dict]:
        """Guardrail 체인 실행. (수정된 답변, 결과 요약) 반환."""
        results = {}
        for name in guardrail_names:
            guardrail = self._guardrails.get(name)
            if not guardrail:
                results[name] = "skipped"
                continue
            try:
                t = time.time()
                result = await guardrail.check(answer, context)
                ms = (time.time() - t) * 1000
                results[name] = {"action": result.action, "ms": round(ms, 1)}

                if result.action == "block":
                    logger.warning(
                        "guardrail_block",
                        guard=name,
                        reason=result.reason,
                    )
                    return GUARDRAIL_BLOCK_TEMPLATE.format(reason=result.reason), results
                if result.action == "warn" and result.modified_answer:
                    logger.info(
                        "guardrail_warn",
                        guard=name,
                        reason=result.reason,
                    )
                    answer = result.modified_answer
            except Exception as e:
                logger.warning("guardrail_error", guard=name, error=str(e))
                results[name] = {"action": "error", "error": str(e)}
        return answer, results

    @staticmethod
    def _build_sources(results: list[dict]) -> list[SourceRef]:
        sources = []
        seen = set()
        for r in results:
            doc_id = r.get("document_id", "")
            if doc_id in seen:
                continue
            seen.add(doc_id)
            sources.append(SourceRef(
                document_id=doc_id,
                title=r.get("title", r.get("file_name", "")),
                chunk_text=r.get("content", "")[:MAX_SOURCE_PREVIEW_LEN],
                score=r.get("score", 0.0),
                method=r.get("method", "vector"),
            ))
        return sources[:MAX_SOURCES]
