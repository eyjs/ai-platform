"""SupervisorState: Supervisor StateGraph의 공유 상태 (P1-0 → P1-2 병렬 확장).

명령형 루프의 지역 변수를 그래프 상태 채널로 승격한 것. `AgentState`
(`src/agent/state.py`)와 동일한 TypedDict 관례를 따른다.

병렬 위임(P1-2, `Send` fan-out)을 위해 `results`/`delegation_log`는
`operator.add` reducer 채널이다 — 같은 슈퍼스텝의 delegate 태스크들이 각자
결과를 append하며, 적용 순서는 Send 생성 순서(=계획 순서)로 결정적이다.

체크포인터는 아직 연결하지 않으므로(단일 요청 in-memory 실행) `ctx`/`budget` 등
비직렬화 객체를 상태에 담아도 안전하다. 체크포인트를 붙일 때 직렬화 경계를
재설계한다.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

from src.domain.agent_context import AgentContext
from src.domain.models import AgentResponse
from src.supervisor.limits import DelegationBudget
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult


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

    # 위임 계획/진행 (decompose → dispatch fan-out → collect → replan 루프)
    plan: Optional[DelegationPlan]
    workflow_policy: str  # "handoff"(단일 위임) | "block"(다중/replan 위임)
    round: int  # 완료된 위임 라운드 수 (collect 노드가 증가)
    budget: Optional[DelegationBudget]

    # 병렬 위임 태스크의 Send payload 전용 키 — delegate 노드만 읽는다.
    # 전역 상태에는 남지 않는다(태스크 입력으로만 존재).
    current_step: Optional[DelegationStep]

    # 위임 결과 누적 (reducer: 병렬 delegate 태스크들이 append)
    results: Annotated[list[SubAgentResult], operator.add]
    # P1-3 위임 트레이스: 위임 1건당 관측 레코드 {profile, reason, ok, error, latency_ms, round}
    delegation_log: Annotated[list[dict], operator.add]

    # 출력 (sticky_delegate 또는 finalize 노드가 채움)
    response: Optional[AgentResponse]
