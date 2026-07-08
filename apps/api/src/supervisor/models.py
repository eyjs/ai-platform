"""Supervisor 공유 데이터 계약 (P0-1).

이 모듈은 이후 모든 supervisor 태스크(task-003/005/006 등)가 import만 하는
확정 계약이다. 여기 정의된 타입은 이 태스크(task-001) 소유이며, 이후 태스크는
편집하지 않고 그대로 소비한다.

**scoped context 규약**: 서브에 넘길 위임 스코프 컨텍스트는 신규 타입을 만들지
않고 기존 `AgentContext`(`src/domain/agent_context.py`)를 그대로 재사용한다.
`derive_scoped_context(ctx, step) -> AgentContext`는 task-005가 구현한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DelegationStep:
    """메인이 결정한 위임 1건.

    hub 강제(§0-5): 이 타입에도 재라우팅 필드(next_profile/route_to 등)를 두지 않는다.
    다음 행동 결정은 오직 메인(main_llm)이 한다.
    """

    profile: str  # 위임 대상 프로파일 id
    subquery: str  # 서브에 전달할 분해된 질의
    reason: str = ""  # 트레이스용 — 메인이 이 서브를 고른 이유
    scope_hint: dict = field(default_factory=dict)  # 서브에 넘길 필요범위 힌트 (task-005 소비)


@dataclass
class DelegationPlan:
    """메인의 위임 계획 (decompose 결과)."""

    delegations: list[DelegationStep]
    # P0에서는 항상 False(순차 위임, replan 없음). P1 adaptive replan 훅 자리만 마련.
    is_adaptive: bool = False


@dataclass
class SubAgentResult:
    """서브 실행 결과. 서브는 오직 이 결과/실패만 메인에 반환한다(§0-5, P0-8).

    **hub 강제 계약(중요)**: 이 타입에는 "다음에 어디로 갈지"를 나타내는 필드
    (next_profile/route_to 등)를 절대 두지 않는다. 서브가 다른 서브로 재라우팅할
    수 있는 코드 경로 자체를 인터페이스 수준에서 차단한다.
    """

    profile: str
    answer: str
    sources: list = field(default_factory=list)
    trace: object | None = None
    ok: bool = True
    error: str | None = None
    # 워크플로우 핸드오프 표식: 서브가 인터랙티브 워크플로우로 실행되어
    # 답변(다음 단계 질문)을 그대로 사용자에게 전달해야 함(synthesize 금지).
    # hub 유지 — "다음 턴을 누가 받나"는 여전히 메인이 sticky 감지로 결정한다.
    workflow_handoff: bool = False


@dataclass(frozen=True)
class SupervisorLimits:
    """위임 상한(P0-6). RAG 재시도 캡과 동일 철학 — 무한 위임 방지.

    task-006이 소비한다. P0는 1-depth 고정.
    """

    max_delegations: int = 4
    max_depth: int = 1  # P0는 1-depth 고정 (서브가 또 Supervisor를 호출하지 않음)
    # 위임 1건의 실행 상한(초). 서브가 응답 없이 잡히면(예: 외부 의존 행)
    # supervise 전체가 무한 대기하며 SSE가 ping만 보내는 사고를 차단한다.
    delegation_timeout_sec: float = 120.0
    # 워크플로우 핸드오프 전용 상한. 로컬 LLM에서 dynamic 스텝(페르소나+맥락 생성)이
    # 스텝당 50~120s+ 걸리는 실측 — 직접 모드와 동일한 인내심을 준다.
    workflow_handoff_timeout_sec: float = 300.0
