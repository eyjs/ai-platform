"""Supervisor: LangGraph StateGraph 기반 위임 오케스트레이션 (P1-0).

P0의 명령형 루프(decompose → 순차 위임 → synthesize)를 StateGraph로 전환했다.
공개 계약은 불변 — 생성자 시그니처와 `supervise()`는 P0과 동일하며, 실행
의미론(관문 위치, 캡, hub 강제, sticky, 핸드오프 passthrough)도 그대로다.
그래프 토폴로지와 노드 구현은 `src/supervisor/graph.py` 참조.

`supervise_stream()`은 같은 그래프를 emitter(asyncio.Queue) 브리지와 함께 돌려
최종 답변 생성 토큰을 실시간으로 흘린다 — 단일 위임 passthrough면 서브 토큰,
다중 위임이면 synthesize 토큰. 비스트리밍 `supervise()`는 emitter 없이 동작해
기존과 완전히 동일하다.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

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

    @staticmethod
    def _initial_state(
        question: str,
        ctx: AgentContext,
        user_ctx,
        trace: Optional[object],
        emitter=None,
    ) -> SupervisorState:
        return {
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
            "round": 0,
            "budget": None,
            "current_step": None,
            "results": [],
            "delegation_log": [],
            "emitter": emitter,
            "streamed_answer": False,
            "response": None,
        }

    async def supervise(
        self,
        question: str,
        ctx: AgentContext,
        user_ctx,
        trace: Optional[object] = None,
    ) -> AgentResponse:
        """질의를 분해해 인가된 서브에 위임하고 결과를 종합한다 (비스트리밍)."""
        final_state = await self._graph.ainvoke(
            self._initial_state(question, ctx, user_ctx, trace),
            config={"recursion_limit": RECURSION_LIMIT},
        )
        return final_state["response"]

    async def supervise_stream(
        self,
        question: str,
        ctx: AgentContext,
        user_ctx,
        trace: Optional[object] = None,
    ) -> AsyncIterator[dict]:
        """supervise()의 토큰 스트리밍 판.

        yield 이벤트:
        - {"type": "token"|"replace", "data": str} — 최종 답변 생성 토큰
        - {"type": "done", "data": {"response": AgentResponse, "streamed": bool}} — 종료 1건.
          `streamed=False`면 토큰이 하나도 안 나간 것(핸드오프/폴백 등) — 호출자가
          답변 전체를 단일 방출해야 한다.
        """
        queue: asyncio.Queue = asyncio.Queue()
        graph_task = asyncio.create_task(
            self._graph.ainvoke(
                self._initial_state(question, ctx, user_ctx, trace, emitter=queue),
                config={"recursion_limit": RECURSION_LIMIT},
            )
        )
        try:
            while True:
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {getter, graph_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if getter in done:
                    kind, data = getter.result()
                    yield {"type": kind, "data": data}
                    if graph_task in done:
                        break
                    continue
                getter.cancel()
                break
            # 그래프 종료 후 큐 잔량 드레인(마지막 토큰 유실 방지).
            while not queue.empty():
                kind, data = queue.get_nowait()
                yield {"type": kind, "data": data}

            final_state = graph_task.result()  # 그래프 예외는 여기서 전파
            yield {
                "type": "done",
                "data": {
                    "response": final_state["response"],
                    "streamed": bool(final_state.get("streamed_answer")),
                },
            }
        finally:
            if not graph_task.done():
                graph_task.cancel()
