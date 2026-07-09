"""Supervisor: LangGraph StateGraph 기반 위임 오케스트레이션 (P1-0).

P0의 명령형 루프(decompose → 순차 위임 → synthesize)를 StateGraph로 전환했다.
공개 계약은 불변 — 생성자 시그니처와 `supervise()`는 P0과 동일하며, 실행
의미론(관문 위치, 캡, hub 강제, sticky, 핸드오프 passthrough)도 그대로다.
그래프 토폴로지와 노드 구현은 `src/supervisor/graph.py` 참조.
"""

from __future__ import annotations

from typing import Optional

from src.domain.agent_context import AgentContext
from src.domain.models import AgentResponse
from src.supervisor.authz import DelegationAuthorizer
from src.supervisor.graph import RECURSION_LIMIT, build_supervisor_graph
from src.supervisor.models import SupervisorLimits
from src.supervisor.planner_llm import SupervisorPlanner
from src.supervisor.state import SupervisorState
from src.supervisor.subagent_runner import SubAgentRunner


class Supervisor:
    """컴파일된 위임 StateGraph를 감싸는 파사드.

    생성자 주입은 합성 루트(task-002)가 배선한다. 그래프는 생성 시 1회
    컴파일되어 요청 간 재사용된다(노드는 stateless — 요청별 상태는
    `SupervisorState`로만 흐른다).
    """

    def __init__(
        self,
        planner: SupervisorPlanner,
        runner: SubAgentRunner,
        authorizer: DelegationAuthorizer,
        limits: SupervisorLimits,
        profile_store,
        workflow_engine=None,
    ) -> None:
        # sticky 감지용(진행 중 서브 워크플로우 우선 재위임). 미주입 시 sticky 비활성.
        self._graph = build_supervisor_graph(
            planner=planner,
            runner=runner,
            authorizer=authorizer,
            limits=limits,
            profile_store=profile_store,
            workflow_engine=workflow_engine,
        )

    async def supervise(
        self,
        question: str,
        ctx: AgentContext,
        user_ctx,
        trace: Optional[object] = None,
    ) -> AgentResponse:
        """질의를 분해해 인가된 서브에 순차 위임하고 결과를 종합한다."""
        initial_state: SupervisorState = {
            "question": question,
            "ctx": ctx,
            "user_ctx": user_ctx,
            "trace": trace,
            "allowed": None,
            "all_profiles": [],
            "candidates": [],
            "supervisor_id": "",
            "sticky_profile": None,
            "plan": None,
            "workflow_policy": "block",
            "step_index": 0,
            "budget": None,
            "results": [],
            "response": None,
        }
        final_state = await self._graph.ainvoke(
            initial_state, config={"recursion_limit": RECURSION_LIMIT}
        )
        return final_state["response"]
