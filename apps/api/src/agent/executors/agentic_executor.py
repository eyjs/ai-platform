"""AgenticExecutorMixin — 에이전틱 모드 실행 메서드 모음.

graph_executor.py 분할 산출물. GraphExecutor MRO 상속 경로:
  GraphExecutor(WorkflowExecutorMixin, DeterministicExecutorMixin, AgenticExecutorMixin)
"""

import asyncio
from typing import AsyncIterator, Optional

from langgraph.errors import GraphRecursionError

from src.agent.graphs import build_agentic_graph
from src.agent.graph_cache import GraphCache
from src.agent.nodes import run_guardrail_chain
from src.agent.tool_adapter import convert_tools_to_langchain
from src.agent.executors._helpers import (
    _content_to_text,
    _extract_faithfulness_score,
    _build_agentic_user_turn,
)
from src.domain.agent_context import AgentContext
from src.domain.execution_plan import ExecutionPlan
from src.domain.models import AgentResponse, TraceInfo
from src.infrastructure.providers.model_aliases import resolve_model_alias
from src.observability.logging import get_logger
from src.observability.trace_logger import RequestTrace
from src.safety.base import GuardrailContext

logger = get_logger(__name__)


class AgenticExecutorMixin:
    """에이전틱 모드(_get_or_build_agentic_graph / _effective_agentic_app / _execute_agentic / _stream_agentic)."""

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

        # P0-4: recursion_limit = 2*max_tool_calls+1 (런어웨이 가드, 정밀 캡이 아님).
        # 정밀 캡은 아래 messages 카운팅으로 구현한다.
        recursion_limit = 2 * plan.max_tool_calls + 1
        invoke_config = {"recursion_limit": recursion_limit}

        # P0-5: agent_timeout_seconds — ainvoke 전체를 asyncio.wait_for 로 래핑한다.
        result = None
        timed_out = False
        recursion_exceeded = False
        try:
            result = await asyncio.wait_for(
                agent_app.ainvoke(
                    {"messages": [{"role": "user", "content": effective_question}]},
                    config=invoke_config,
                ),
                timeout=plan.agent_timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            logger.warning(
                "agentic_timeout",
                timeout=plan.agent_timeout_seconds,
                question_prefix=question[:80],
            )
        except GraphRecursionError:
            recursion_exceeded = True
            logger.warning(
                "agentic_tool_cap_reached_via_recursion",
                max=plan.max_tool_calls,
                recursion_limit=recursion_limit,
                question_prefix=question[:80],
            )

        if timed_out:
            return AgentResponse(
                answer="응답 생성이 지연되어 안전하게 중단했어요. 잠시 후 다시 시도해 주세요.",
                sources=[],
                trace=TraceInfo(
                    question_type=plan.question_type.value,
                    mode="agentic",
                    tools_called=[],
                ),
            )

        # 결과 추출
        answer = ""
        tools_called = []
        if result and "messages" in result:
            for msg in result["messages"]:
                if hasattr(msg, "type") and msg.type == "tool":
                    tools_called.append(msg.name if hasattr(msg, "name") else "unknown")

            # 마지막 AI 메시지가 최종 답변
            last_msg = result["messages"][-1]
            if hasattr(last_msg, "content"):
                answer = _content_to_text(last_msg.content)

        # P0-4 정밀 캡: tool 메시지 수 > max_tool_calls 이면 캡에 걸린 것으로 처리한다.
        # GraphRecursionError 로 중단된 경우도 이미 수집된 tool 수로 경고를 기록한다.
        tool_count = len(tools_called)
        if recursion_exceeded or tool_count > plan.max_tool_calls:
            logger.warning(
                "agentic_tool_cap_reached",
                max=plan.max_tool_calls,
                called=tool_count,
                question_prefix=question[:80],
            )

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

        # P0-4: recursion_limit = 2*max_tool_calls+1 (런어웨이 가드, 정밀 캡이 아님).
        # 정밀 캡은 on_tool_start 이벤트 카운팅으로 구현한다.
        recursion_limit = 2 * plan.max_tool_calls + 1
        stream_config = {"recursion_limit": recursion_limit}

        # P0-4/P0-5 상태 플래그
        _tool_cap_reached = False
        _stream_timed_out = False
        _tool_start_count = 0  # on_tool_start 카운터 (정밀 캡용)

        # P0-5: asyncio.timeout (Python 3.11+ / 이 venv는 3.13) 으로 전체 스트림 소비를
        # 타임아웃으로 제한한다. 타임아웃 시 이미 yield 된 토큰(answer)은 보존된다.
        # on TimeoutError: 부분 토큰 보존 + 친화적 close 토큰 + clean done 이벤트를 yield 한다.
        try:
            async with asyncio.timeout(plan.agent_timeout_seconds):
                async for event in agent_app.astream_events(
                    {"messages": [{"role": "user", "content": effective_question}]},
                    version="v2",
                    config=stream_config,
                ):
                    kind = event.get("event", "")

                    if kind == "on_tool_start":
                        _tool_start_count += 1
                        # P0-4 정밀 캡: 카운터가 한도를 초과하면 스트림 소비를 중단한다.
                        if _tool_start_count > plan.max_tool_calls:
                            _tool_cap_reached = True
                            logger.warning(
                                "agentic_tool_cap_reached",
                                max=plan.max_tool_calls,
                                called=_tool_start_count,
                                question_prefix=question[:80],
                            )
                            break
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

        except TimeoutError:
            # P0-5 타임아웃: 이미 yield 된 부분 토큰(answer)은 보존된다.
            # 친화적 close 토큰을 추가로 yield 하여 사용자가 중단됐음을 알 수 있게 한다.
            # done 이벤트는 아래 공통 경로에서 yield 된다 — SSE 스트림이 열린 채로 남지 않도록.
            _stream_timed_out = True
            logger.warning(
                "agentic_stream_timeout",
                timeout=plan.agent_timeout_seconds,
                partial_tokens=len(answer),
                question_prefix=question[:80],
            )
            close_msg = "\n\n_(응답 생성이 지연되어 안전하게 중단했어요. 잠시 후 다시 시도해 주세요.)_"
            yield {"type": "token", "data": close_msg}
            answer += close_msg

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
