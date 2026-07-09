"""SupervisorState: Supervisor StateGraph의 공유 상태 (P1-0).

명령형 루프의 지역 변수를 그래프 상태 채널로 승격한 것. `AgentState`
(`src/agent/state.py`)와 동일한 TypedDict 관례를 따른다.

체크포인터는 아직 연결하지 않으므로(단일 요청 in-memory 실행) `ctx`/`budget` 등
비직렬화 객체를 상태에 담아도 안전하다. P1에서 체크포인트를 붙일 때 직렬화
경계를 재설계한다.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from src.domain.agent_context import AgentContext
from src.domain.models import AgentResponse
from src.supervisor.limits import DelegationBudget
from src.supervisor.models import DelegationPlan, SubAgentResult


class SupervisorState(TypedDict):
    """Supervisor 위임 그래프 상태."""

    # 입력 (supervise 호출 시 고정)
    question: str
    ctx: AgentContext
    user_ctx: Any
    trace: Optional[object]

    # 스코프 해석 (resolve_scope 노드 산출)
    allowed: Optional[set[str]]  # None=전체 허용, set=deny-by-default 허용 목록
    all_profiles: list
    candidates: list[dict]
    supervisor_id: str

    # sticky 감지 (detect_sticky 노드 산출)
    sticky_profile: Optional[str]

    # 위임 계획/진행 (decompose → delegate 루프)
    plan: Optional[DelegationPlan]
    workflow_policy: str  # "handoff"(단일 위임) | "block"(다중 위임)
    step_index: int  # 다음에 처리할 plan.delegations 인덱스
    budget: Optional[DelegationBudget]
    results: list[SubAgentResult]

    # 출력 (sticky_delegate 또는 finalize 노드가 채움)
    response: Optional[AgentResponse]
