"""LangGraph 노드 팩토리 함수.

ai-worker 패턴: 팩토리 함수가 의존성을 클로저로 캡처 -> 순수 노드 함수 반환.
"""

import asyncio
import re
import time
from typing import Callable

from collections import defaultdict

from src.agent.state import AgentState
from src.agent.planner import _validate_steps
from src.config import settings
from src.infrastructure.providers.base import LLMProvider
from src.locale.bundle import get_locale
from src.observability.logging import get_logger
from src.domain.execution_plan import ToolCall
from src.safety.base import Guardrail, GuardrailContext
from src.domain.agent_context import AgentContext
from src.tools.registry import ToolRegistry

logger = get_logger(__name__)

MAX_CONTENT_PREVIEW_LEN = 2500
MAX_SOURCE_PREVIEW_LEN = 200
MAX_SOURCES = 5
MIN_SOURCE_SCORE = 0.3


# --- 헬퍼 함수 ---


def _top_results_by_score(results: list[dict], max_chunks: int) -> list[dict]:
    """score 상위 max_chunks개 선별.

    graph_enrich가 결과를 리스트 '뒤에' append하므로 정렬 없이 앞에서 자르면
    그래프축 청크가 항상 잘려나간다(스트리밍 경로와 동일 규칙 — 실사고 수정).
    """
    return sorted(results, key=lambda r: r.get("score", 0.0), reverse=True)[:max_chunks]


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


# 검색 결과 충분성 임계 — 리랭킹 여부에 따라 점수 스케일이 다르다.
#   리랭킹됨: fused_score 0~1 스케일 / 리랭킹 스킵·실패: RRF ~0.01 스케일.
# 단일 0.4 임계는 리랭킹 안 탄(후보 ≤ top_k, 리랭커 없음/degrade) 결과를 아무리
# 관련성이 높아도 '불충분'으로 오판해 상시 재시도를 유발했다(스케일 불일치 버그).
RERANKED_SUFFICIENT_SCORE = 0.4
RRF_SUFFICIENT_SCORE = 0.012  # rag_search PROBE_SKIP_THRESHOLD 와 정합


def _results_sufficient(results: list[dict]) -> bool:
    """스케일 인지 충분성 판정. rerank_score 유무로 점수 스케일을 구분한다."""
    if not results:
        return False
    top_score = max(r.get("score", 0.0) for r in results)
    reranked = any("rerank_score" in r for r in results)
    threshold = RERANKED_SUFFICIENT_SCORE if reranked else RRF_SUFFICIENT_SCORE
    return top_score >= threshold


def route_by_evaluation(state: AgentState) -> str:
    """검색 결과 품질(스케일 인지)에 따라 다음 노드를 결정한다.

    - 결과 충분: generate_with_context
    - 빈 결과(0건): 재시도 스킵 → generate_with_context (rewrite해도 0건 반복, 실측 근거)
    - 결과 불충분 & retry 가능: rewrite_query
    - 결과 불충분 & retry 소진: generate_with_context (best-effort)
    """
    results = state.get("search_results", [])
    retry_count = state.get("retry_count", 0)

    if _results_sufficient(results):
        return "generate_with_context"

    # 빈 결과는 재시도해도 무의미(쿼리 재작성으로 없던 문서가 생기지 않음) → best-effort 생성
    if not results:
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


def _inject_strategy_params(
    tc: ToolCall, registry: ToolRegistry, strategy,
    rag_min_rerank_score: float | None = None,
) -> ToolCall:
    """도구 스키마가 선언했지만 계획이 누락한 전략/프로필 파라미터를 주입한다.

    실사고(배선 갭): strategy.max_vector_chunks(예: CROSS_DOC=10)가 ToolCall
    params에 실리지 않아 rag_search가 항상 기본 top_k(5)로 검색했다 — 프롬프트
    슬롯만 10개로 늘던 반쪽 배선. 도구 이름 하드코딩 대신 input_schema 선언
    기반으로 주입해 Tool 격리 원칙을 지킨다.

    같은 원리로 프로필별 rag_min_rerank_score(관련도 하한)도 스키마에 선언된
    경우에만 주입한다 — 전역 상수 대신 도메인별 프로필 오버라이드가 되게.
    """
    try:
        tool = registry.get(tc.tool_name)
        schema = getattr(tool, "input_schema", None)
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    except Exception:
        props = {}

    injected = dict(tc.params)
    max_chunks = getattr(strategy, "max_vector_chunks", None)
    if (
        max_chunks is not None
        and "max_vector_chunks" not in tc.params
        and "max_vector_chunks" in props
    ):
        injected["max_vector_chunks"] = max_chunks
    if (
        rag_min_rerank_score is not None
        and "min_rerank_score" not in tc.params
        and "min_rerank_score" in props
    ):
        injected["min_rerank_score"] = rag_min_rerank_score

    if injected == tc.params:
        return tc
    return ToolCall(tool_name=tc.tool_name, params=injected)


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
                # 도구가 반환한 관측 상세(rag_search: 필터·후보·리랭킹 청크)를
                # 트레이스 노드에 실어 요약(latency_breakdown)으로 영속화한다.
                detail = result.metadata.get("trace_detail") if result.metadata else None
                trace_node.finish(
                    success=result.success,
                    chunks=len(result.data) if result.data else 0,
                    **({"detail": detail} if detail else {}),
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

        strategy = getattr(plan, "strategy", None)
        plan_floor = getattr(plan, "rag_min_rerank_score", None)
        for group in tool_groups:
            group = [
                _inject_strategy_params(tc, registry, strategy, plan_floor) for tc in group
            ]
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
                detail = result.metadata.get("trace_detail") if result.metadata else None
                tool_latencies.append({
                    "tool": tc.tool_name, "success": result.success,
                    "chunks_found": chunks_found, "ms": round(node_ms, 1),
                    **({"detail": detail} if detail else {}),
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

        # 결과가 충분하면 그대로 진행 (스케일 인지 판정)
        if _results_sufficient(results):
            return {"retry_count": retry_count}

        # 빈 결과: 재시도해도 0건 반복 → 증가시키지 않아 route에서 best-effort 생성으로 간다
        if not results:
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
        plan = state["plan"]

        # 인가 경계: 재작성 LLM 출력은 프로필이 이 계획에 허용한 도구 집합으로
        # 검증한다. 무검증 실행은 (a) 누락 키로 KeyError 크래시, (b) 환각 도구명이
        # 레지스트리의 전역 도구를 호출하는 프로필 권한 우회를 허용했다.
        allowed_tools = {
            tc.tool_name for group in plan.tool_groups for tc in group
        }

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
            valid_steps = _validate_steps(new_steps, allowed_tools) if new_steps else []
            if valid_steps:
                logger.info("rewrite_query_success", reasoning=reasoning[:100])
                return {
                    "planned_steps": valid_steps,
                    "planning_reasoning": f"retry: {reasoning}",
                }
            if new_steps:
                logger.warning(
                    "rewrite_query_no_valid_steps",
                    raw_steps=len(new_steps), allowed=sorted(allowed_tools),
                )
        except Exception as e:
            logger.warning("rewrite_query_failed", error=str(e))

        # 실패 시 원래 질문으로 rag_search 재실행 (인가 경계 준수)
        if "rag_search" not in allowed_tools:
            logger.warning(
                "rewrite_fallback_tool_not_allowed", allowed=sorted(allowed_tools),
            )
            return {}
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
        prompt_results = _top_results_by_score(results, max_chunks)
        prompt = build_prompt(question, plan, prompt_results)

        # guardrail 피드백은 per-turn 지시 → volatile 로 분리.
        # plan.system_prompt 는 cacheable(persona+grounding), 피드백은 volatile_system 에 추가.
        guardrail_feedback = (
            f"[IMPORTANT] 이전 답변이 품질 검증에서 부족한 평가를 받았습니다 "
            f"({warning_text}). "
            f"참고 문서에 충실하게 답변하세요. 근거 없는 내용은 포함하지 마세요."
        )
        volatile_extra = (
            f"{plan.volatile_system_prompt}\n\n{guardrail_feedback}"
            if plan.volatile_system_prompt
            else guardrail_feedback
        )

        answer = await llm.generate(
            prompt,
            cacheable_system=plan.system_prompt,
            volatile_system=volatile_extra,
        )
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
        prompt_results = _top_results_by_score(results, max_chunks)

        # 관련도 게이트: needs_rag인데 관련 컨텍스트가 없으면 LLM을 태우지 않고
        # 정직 반려(무관 청크로 지어내기 방지 — 스트리밍 경로와 동일 계약).
        from src.agent.executors._helpers import insufficient_context_refusal
        refusal = insufficient_context_refusal(plan, prompt_results)
        if refusal is not None:
            logger.info(
                "insufficient_context_refusal",
                context_chunks=len(prompt_results), needs_rag=plan.strategy.needs_rag,
            )
            return {"answer": refusal}

        prompt = build_prompt(question, plan, prompt_results)
        # 비스트리밍 생성도 트레이스에 기록 — 없으면 graph_execute 총합만 보여
        # 지배적 지연(생성)이 breakdown에서 사라진다 (스트리밍 경로와 동일 계약).
        trace = state.get("trace")
        trace_node = trace.start_node("generate_with_context") if trace else None
        # plan.system_prompt = cacheable(persona+grounding), plan.volatile_system_prompt = 날짜.
        answer = await llm.generate(
            prompt,
            cacheable_system=plan.system_prompt,
            volatile_system=plan.volatile_system_prompt,
            max_tokens=plan.max_output_tokens,
        )
        if trace_node:
            trace_node.finish(answer_len=len(answer), chunks=len(prompt_results))

        logger.info("llm_generate", answer_len=len(answer), context_chunks=len(prompt_results))
        # 프롬프트에 실린 번호 목록을 남긴다 — 인용 검증([n] 범위)과 [n]→파일명 치환의
        # 정본. 모델이 본 것과 검증하는 것이 다르면 검증이 거짓말을 한다.
        return {"answer": answer, "prompt_documents": prompt_results}

    return generate_with_context


def create_direct_generate(llm: LLMProvider) -> Callable:
    """직접 답변 생성 노드 (RAG 불필요)."""

    async def direct_generate(state: AgentState) -> dict:
        question = state["question"]
        plan = state["plan"]
        # plan.system_prompt = cacheable(persona), plan.volatile_system_prompt = 날짜.
        answer = await llm.generate(
            question,
            cacheable_system=plan.system_prompt,
            volatile_system=plan.volatile_system_prompt,
            max_tokens=plan.max_output_tokens,
        )
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
            prompt_documents=state.get("prompt_documents") or [],
            profile_id=state["session_id"],
            response_policy=plan.response_policy,
        )

        trace = state.get("trace")
        trace_node = trace.start_node("run_guardrails") if trace else None
        answer, results = await run_guardrail_chain(
            answer, plan.guardrail_chain, guardrails, context,
        )
        if trace_node:
            trace_node.finish()

        # Guardrail 재생성 판단: warn + score < 0.35이면 재생성 후보
        # (0.5에서 0.35로 하향: 과도한 재생성 루프 억제)
        regenerate_needed = any(
            isinstance(v, dict) and v.get("action") == "warn"
            and v.get("score") is not None and v.get("score") < 0.35
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
        answer = render_citations(state.get("answer") or "", state.get("prompt_documents") or [])
        return {"sources": sources, "answer": answer}

    return build_response


# 모델이 쓰는 인용 토큰. ko.yaml이 "번호만 쓰라"고 지시한다.
_CITATION_TOKEN_RE = re.compile(r'\[(\d{1,2})\]')


def build_citation_map(prompt_documents: list[dict]) -> list[dict]:
    """인용 번호 → 문서 매핑. 스트리밍 소비자가 [n]을 렌더링할 표.

    스트리밍은 토큰이 이미 나간 뒤라 서버가 [n]을 치환할 수 없다(비스트리밍은
    build_response가 치환한다). 대신 이 표를 done에 실어 소비자가 붙이게 한다 —
    번호를 살려두면 인용을 클릭 가능한 칩으로 렌더링할 수도 있다.

    sources(build_source_dicts)는 **문서 단위로 중복 제거**돼 번호와 1:1이 아니다.
    [3]을 sources[2]로 매핑하면 엉뚱한 문서를 가리킨다 — 그래서 이 표가 따로 있다.
    """
    return [
        {
            "n": i,
            "document_id": doc.get("document_id"),
            "file_name": doc.get("file_name") or doc.get("title") or "",
        }
        for i, doc in enumerate(prompt_documents, 1)
    ]


def render_citations(answer: str, prompt_documents: list[dict]) -> str:
    """답변의 [n]을 사람이 읽을 파일명으로 치환한다.

    모델에게는 번호로만 인용시키고(ko.yaml), 사람에게 보일 때 이름을 붙인다.
    이 분리가 요점이다 — 모델이 긴 한글 파일명을 재현하게 하면 철자가 흔들리고,
    그걸 문자열로 검증하려다 오탐 지옥에 빠진다(2026-07-16 실사고).
    번호는 프롬프트가 붙였으므로 매핑은 완전일치다. LIKE 검색이 필요 없다.

    범위를 벗어난 번호는 **건드리지 않는다** — 지어낸 인용을 그럴듯한 파일명으로
    바꿔주면 환각을 감춰주는 꼴이다. 그건 faithfulness 가드가 잡아 남긴다.
    """
    if not answer or not prompt_documents:
        return answer

    def _label(m: re.Match) -> str:
        n = int(m.group(1))
        if not 1 <= n <= len(prompt_documents):
            return m.group(0)  # 조작 인용 — 원문 그대로 두고 가드가 신고한다
        doc = prompt_documents[n - 1]
        name = doc.get("file_name") or doc.get("title") or ""
        return f"[출처: {name}]" if name else m.group(0)

    return _CITATION_TOKEN_RE.sub(_label, answer)


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
                # 재생성 피드백·관측용 판정 사유 (pass면 None)
                "reason": result.reason,
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
    """검색 결과를 중복 제거된 출처 dict 리스트로 변환한다.

    스케일 인지: 절대 임계(MIN_SOURCE_SCORE=0.3)는 리랭킹된 fused(0~1)
    스케일에서만 유효하다. 리랭킹 스킵/degrade 경로의 RRF(~0.01) 스케일에
    같은 임계를 적용하면 출처가 전멸한다(_results_sufficient와 동일 버그 부류).
    비리랭킹 경로는 임계 없이 dedupe+상한(MAX_SOURCES)에 맡긴다 — 어차피
    같은 청크가 LLM 컨텍스트로 들어가므로 출처로 보이는 것이 정직하다.
    """
    # graph_enrich 등이 뒤에 append하므로 점수순 정렬 후 상위 출처를 뽑는다
    ordered = sorted(results, key=lambda r: r.get("score", 0.0), reverse=True)
    reranked = any("rerank_score" in r for r in ordered)
    min_score = MIN_SOURCE_SCORE if reranked else 0.0

    sources = []
    seen: set[str] = set()
    for r in ordered:
        score = r.get("score", 0.0)
        if score < min_score:
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
