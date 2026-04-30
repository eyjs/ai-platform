"""LangGraph 그래프 빌더.

결정론적(StateGraph) + 에이전틱(create_react_agent) 그래프를 빌드한다.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent

from typing import Optional

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

    # --- 노드 등록 ---
    workflow.add_node(
        "plan_execution",
        create_planner(llm, registry.resolve),
    )
    workflow.add_node("execute_tools", create_execute_tools(registry))

    has_graph = kms_graph_client is not None and vector_store is not None
    if has_graph:
        workflow.add_node(
            "graph_enrich",
            create_graph_enrich(kms_graph_client, vector_store),
        )

    workflow.add_node("evaluate_results", create_evaluate_results())
    workflow.add_node("rewrite_query", create_rewrite_query(llm))
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
):
    """에이전틱 ReAct 그래프.

    create_react_agent로 LLM이 도구를 자율 선택한다.
    Guardrail은 GraphExecutor에서 에이전트 실행 후 적용.

    Args:
        chat_model: LangChain ChatModel (tool calling 지원)
        tools: LangChain 도구 목록
        system_prompt: 시스템 프롬프트

    Returns:
        CompiledGraph (ainvoke/astream 가능)
    """
    if not tools:
        raise ValueError("에이전틱 모드에는 최소 1개 이상의 도구가 필요합니다.")

    return create_react_agent(
        model=chat_model,
        tools=tools,
        prompt=system_prompt if system_prompt else None,
    )
