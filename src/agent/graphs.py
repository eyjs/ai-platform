"""LangGraph 그래프 빌더.

결정론적(StateGraph) + 에이전틱(create_react_agent) 그래프를 빌드한다.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent

from src.agent.nodes import (
    create_build_response,
    create_direct_generate,
    create_execute_tools,
    create_generate_with_context,
    create_run_guardrails,
    route_by_rag,
)
from src.agent.state import AgentState
from src.infrastructure.providers.base import LLMProvider
from src.safety.base import Guardrail
from src.tools.registry import ToolRegistry


def build_deterministic_graph(
    llm: LLMProvider,
    registry: ToolRegistry,
    guardrails: dict[str, Guardrail],
) -> StateGraph:
    """결정론적 RAG 파이프라인 그래프.

    START -> route -+-> execute_tools -> generate_with_context -> run_guardrails -> build_response -> END
                    +-> direct_generate -> END
    """
    workflow = StateGraph(AgentState)

    # 노드 등록
    workflow.add_node("execute_tools", create_execute_tools(registry))
    workflow.add_node("generate_with_context", create_generate_with_context(llm))
    workflow.add_node("direct_generate", create_direct_generate(llm))
    workflow.add_node("run_guardrails", create_run_guardrails(guardrails))
    workflow.add_node("build_response", create_build_response())

    # 엣지 연결
    workflow.set_conditional_entry_point(
        route_by_rag,
        {
            "execute_tools": "execute_tools",
            "direct_generate": "direct_generate",
        },
    )

    # RAG 경로: tools -> generate -> guardrails -> build -> END
    workflow.add_edge("execute_tools", "generate_with_context")
    workflow.add_edge("generate_with_context", "run_guardrails")
    workflow.add_edge("run_guardrails", "build_response")
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
