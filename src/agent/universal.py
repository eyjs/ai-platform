"""Universal Agent: 결정론적 RAG 파이프라인 (MVP).

ExecutionPlan을 받아 Tool 실행 → LLM 답변 생성 → Guardrail 체인.
LangGraph StateGraph 기반.
"""

import logging
import time
from typing import Any, List, Optional, Union

from src.gateway.models import ChatResponse, SourceRef, TraceInfo
from src.infrastructure.providers.base import LLMProvider
from src.router.execution_plan import ExecutionPlan, QuestionType
from src.safety.base import Guardrail, GuardrailContext, GuardrailResult
from src.tools.base import AgentContext, ScopedTool, Tool, ToolResult
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


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
    ) -> ChatResponse:
        """ExecutionPlan 기반 실행."""
        start_time = time.time()
        tools_called = []
        all_results: list[ToolResult] = []

        # 1. RAG 불필요 (인사, 시스템 질문)
        if not plan.strategy.needs_rag:
            answer = await self._generate_direct(question, plan)
            return ChatResponse(
                answer=answer,
                trace=TraceInfo(
                    question_type=plan.question_type.value,
                    mode=plan.mode,
                    latency_ms=(time.time() - start_time) * 1000,
                ),
            )

        # 2. Tool 실행 (순서대로)
        for tool in plan.tools:
            tool_name = tool.name if hasattr(tool, "name") else str(tool)
            tools_called.append(tool_name)

            result = await self._registry.execute(
                tool_name=tool_name,
                params={"query": question},
                context=context,
                scope=plan.scope,
            )
            all_results.append(result)

        # 3. 검색 결과 수집
        search_results = []
        for result in all_results:
            if result.success and isinstance(result.data, list):
                search_results.extend(result.data)

        # 4. LLM 답변 생성
        answer = await self._generate_with_context(
            question, plan, search_results,
        )

        # 5. Guardrail 체인
        guardrail_context = GuardrailContext(
            question=question,
            source_documents=search_results,
            profile_id=context.session_id,
            response_policy=plan.system_prompt,  # response_policy는 plan에서
        )
        answer = await self._run_guardrails(
            answer, plan.guardrail_chain, guardrail_context,
        )

        # 6. 출처 생성
        sources = self._build_sources(search_results)

        return ChatResponse(
            answer=answer,
            sources=sources,
            trace=TraceInfo(
                question_type=plan.question_type.value,
                mode=plan.mode,
                tools_called=tools_called,
                latency_ms=(time.time() - start_time) * 1000,
            ),
        )

    async def execute_stream(
        self,
        question: str,
        plan: ExecutionPlan,
        context: AgentContext,
    ):
        """SSE 스트리밍 실행. token을 yield한다."""
        tools_called = []
        all_results: list[ToolResult] = []

        # RAG 불필요
        if not plan.strategy.needs_rag:
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
            result = await self._registry.execute(
                tool_name=tool_name,
                params={"query": question},
                context=context,
                scope=plan.scope,
            )
            all_results.append(result)
            yield {"type": "trace", "data": {"tool": tool_name, "success": result.success}}

        # 검색 결과
        search_results = []
        for result in all_results:
            if result.success and isinstance(result.data, list):
                search_results.extend(result.data)

        # 스트리밍 답변 생성
        prompt = self._build_prompt(question, plan, search_results)
        yield {"type": "trace", "data": {"step": "generation", "status": "start"}}

        async for token in self._main_llm.generate_stream(
            prompt, system=plan.system_prompt,
        ):
            yield {"type": "token", "data": token}

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
    def _build_prompt(question: str, plan: ExecutionPlan, results: list[dict]) -> str:
        if not results:
            return f"질문: {question}\n\n관련 문서를 찾지 못했습니다."

        context_parts = []
        for i, r in enumerate(results[:10], 1):
            title = r.get("title", r.get("file_name", ""))
            content = r.get("content", "")[:500]
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
    ) -> str:
        for name in guardrail_names:
            guardrail = self._guardrails.get(name)
            if not guardrail:
                continue
            try:
                result = await guardrail.check(answer, context)
                if result.action == "block":
                    return f"답변을 제공할 수 없습니다. 사유: {result.reason}"
                if result.action == "warn" and result.modified_answer:
                    answer = result.modified_answer
            except Exception as e:
                logger.warning("Guardrail '%s' failed: %s", name, e)
        return answer

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
                chunk_text=r.get("content", "")[:200],
                score=r.get("score", 0.0),
                method=r.get("method", "vector"),
            ))
        return sources[:5]
