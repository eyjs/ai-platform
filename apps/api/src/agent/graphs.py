"""LangGraph 그래프 빌더.

결정론적(StateGraph) + 에이전틱(create_react_agent) 그래프를 빌드한다.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent

from typing import Optional

from src.common.cache_padding import pad_to_min
from src.agent.graph_enrich import create_graph_enrich
from src.agent.nodes import (
    create_build_response,
    create_direct_generate,
    create_evaluate_results,
    create_execute_tools,
    create_generate_with_context,
    create_regenerate,
    create_rewrite_query,
    create_run_guardrails,
    route_by_evaluation,
    route_by_guardrail,
    route_by_rag,
)
from src.agent.planner import create_planner
from src.agent.state import AgentState
from src.infrastructure.providers.base import LLMProvider
from src.infrastructure.vector_store import VectorStore
from src.safety.base import Guardrail
from src.services.kms_graph_client import KmsGraphClient
from src.tools.registry import ToolRegistry


def build_deterministic_graph(
    llm: LLMProvider,
    registry: ToolRegistry,
    guardrails: dict[str, Guardrail],
    kms_graph_client: Optional[KmsGraphClient] = None,
    vector_store: Optional[VectorStore] = None,
    orchestration_llm: Optional[LLMProvider] = None,
) -> StateGraph:
    """결정론적 RAG 파이프라인 그래프 (Plan-and-Execute + Adaptive Retry + Guardrail Regen).

    전체 토폴로지:
    START -> route_by_rag
      +-> plan_execution -> execute_tools -> [graph_enrich?] -> evaluate_results
          --(conditional: route_by_evaluation)-->
              generate_with_context (충분 or max retry)
              rewrite_query -> execute_tools (retry loop)
          -> generate_with_context -> run_guardrails
          --(conditional: route_by_guardrail)-->
              build_response (정상)
              regenerate -> run_guardrails (1회 재검증)
          -> build_response -> END
      +-> direct_generate -> END

    핵심 기능:
    - plan_execution: Planner LLM이 실행 계획(steps) 생성. 비활성/타임아웃 시 폴백.
    - evaluate_results: 검색 결과 품질 평가 (score >= 0.4 기준).
    - rewrite_query: 품질 불충분 시 LLM에게 쿼리 재작성 요청 (최대 2회).
    - regenerate: guardrail warn + score < 0.5 시 답변 재생성 (최대 1회).
    """
    workflow = StateGraph(AgentState)

    # 오케스트레이션(계획수립·쿼리재작성)은 생성과 분리 — 경량 모델로 라이트사이징.
    # 미주입 시 llm 으로 폴백(하위호환). 생성 노드는 항상 llm(대형)을 유지한다.
    orch_llm = orchestration_llm or llm

    # --- 노드 등록 ---
    workflow.add_node(
        "plan_execution",
        create_planner(orch_llm, registry.resolve),
    )
    workflow.add_node("execute_tools", create_execute_tools(registry))

    has_graph = kms_graph_client is not None and vector_store is not None
    if has_graph:
        workflow.add_node(
            "graph_enrich",
            create_graph_enrich(kms_graph_client, vector_store),
        )

    workflow.add_node("evaluate_results", create_evaluate_results())
    workflow.add_node("rewrite_query", create_rewrite_query(orch_llm))
    workflow.add_node("generate_with_context", create_generate_with_context(llm))
    workflow.add_node("direct_generate", create_direct_generate(llm))
    workflow.add_node("run_guardrails", create_run_guardrails(guardrails))
    workflow.add_node("regenerate", create_regenerate(llm))
    workflow.add_node("build_response", create_build_response())

    # --- 엣지 연결 ---

    # 진입점: needs_rag에 따라 분기
    workflow.set_conditional_entry_point(
        route_by_rag,
        {
            "plan_execution": "plan_execution",
            "direct_generate": "direct_generate",
        },
    )

    # RAG 경로: plan -> execute -> [graph_enrich?] -> evaluate
    workflow.add_edge("plan_execution", "execute_tools")

    if has_graph:
        workflow.add_edge("execute_tools", "graph_enrich")
        workflow.add_edge("graph_enrich", "evaluate_results")
    else:
        workflow.add_edge("execute_tools", "evaluate_results")

    # Adaptive Retry: evaluate -> (conditional) -> generate 또는 rewrite
    workflow.add_conditional_edges(
        "evaluate_results",
        route_by_evaluation,
        {
            "generate_with_context": "generate_with_context",
            "rewrite_query": "rewrite_query",
        },
    )
    # rewrite -> execute (retry loop)
    workflow.add_edge("rewrite_query", "execute_tools")

    # 생성 -> guardrail
    workflow.add_edge("generate_with_context", "run_guardrails")

    # Guardrail Regeneration: guardrail -> (conditional) -> build_response 또는 regenerate
    workflow.add_conditional_edges(
        "run_guardrails",
        route_by_guardrail,
        {
            "build_response": "build_response",
            "regenerate": "regenerate",
        },
    )
    # regenerate -> run_guardrails (1회 재검증)
    workflow.add_edge("regenerate", "run_guardrails")

    workflow.add_edge("build_response", END)

    # 직접 응답 경로: direct -> END
    workflow.add_edge("direct_generate", END)

    return workflow


def build_agentic_graph(
    chat_model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str = "",
    *,
    enable_prompt_cache: bool = True,
    cache_padding_text: str = "",
):
    """에이전틱 ReAct 그래프.

    create_react_agent로 LLM이 도구를 자율 선택한다.
    Guardrail은 GraphExecutor에서 에이전트 실행 후 적용.

    프롬프트 캐싱: ChatAnthropic 백엔드면 system_prompt(cacheable: 페르소나+grounding)를
    `cache_control: ephemeral` content-block으로 감싼다. 날짜/directive 등 volatile은
    여기 굽지 않고 호출부가 user 턴에 주입한다(컴파일 그래프 byte-stable 유지 → prefix 캐시 워밍).

    Args:
        chat_model: LangChain ChatModel (tool calling 지원)
        tools: LangChain 도구 목록
        system_prompt: 캐시 가능 시스템 프롬프트(페르소나+grounding)
        enable_prompt_cache: Anthropic 프롬프트 캐싱 적용 여부

    Returns:
        CompiledGraph (ainvoke/astream 가능)
    """
    if not tools:
        raise ValueError("에이전틱 모드에는 최소 1개 이상의 도구가 필요합니다.")

    # 캐시 경계: ChatAnthropic 일 때만 content-block + cache_control 적용.
    # Haiku 캐시 최소 4096토큰 미달 시 세션 안정 콘텐츠로 패딩(deterministic 경로와 동일).
    cacheable_text = pad_to_min(system_prompt, filler=cache_padding_text) if system_prompt else ""
    use_cache = (
        enable_prompt_cache
        and cacheable_text
        and type(chat_model).__name__ == "ChatAnthropic"
    )

    if use_cache:
        prompt = SystemMessage(content=[{
            "type": "text",
            "text": cacheable_text,
            "cache_control": {"type": "ephemeral"},
        }])
    else:
        prompt = system_prompt or None

    return create_react_agent(
        model=chat_model,
        tools=tools,
        prompt=prompt,
    )
