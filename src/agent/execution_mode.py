"""Agent 실행 경로 정의.

Subagent(기본) / Fork / Team 3가지 실행 경로를 Enum으로 관리한다.
Fork, Team은 장기 계획 항목으로 타입 정의만 선제 확보한다.
"""

from enum import Enum


class AgentExecutionPath(str, Enum):
    """Agent 실행 경로."""

    SUBAGENT = "subagent"
    FORK = "fork"       # TODO(FR-4.2): 부모 컨텍스트 상속 + 프롬프트 캐시 공유
    TEAM = "team"       # TODO(FR-4.3): 메일박스 통신 (비동기 메시지)
