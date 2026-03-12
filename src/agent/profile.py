"""AgentProfile: 도메인별 AI 에이전트 설정 모델.

ChatGPT의 GPTs 설정과 동일한 개념.
Agent는 하나(Universal Agent)이고, Profile만 바꾸면 행동이 바뀐다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.models import AgentMode, ResponsePolicy, SecurityLevel  # noqa: F401 (re-export)


@dataclass(frozen=True)
class ToolRef:
    """Profile에서 참조하는 도구."""

    name: str
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class IntentHint:
    """Profile별 커스텀 Intent 정의.

    기본 QuestionType 외에 도메인 특화 Intent를 선택적으로 추가.
    """

    name: str
    patterns: list[str]
    description: str


@dataclass(frozen=True)
class HybridTrigger:
    """Hybrid 모드에서 워크플로우 진입 조건."""

    keyword_patterns: list[str]
    intent_types: list[str]
    workflow_id: str


@dataclass(frozen=True)
class AgentProfile:
    """도메인별 AI 에이전트 설정.

    Profile과 Domain은 M:N 관계:
    - 하나의 Profile이 여러 Domain을 커버 가능
    - 하나의 Domain이 여러 Profile에서 참조 가능
    """

    id: str
    name: str

    # 도메인 스코프
    domain_scopes: list[str]
    category_scopes: list[str] = field(default_factory=list)
    security_level_max: str = SecurityLevel.PUBLIC
    include_common: bool = True  # 플랫폼 공통 지식(_common) 포함 여부

    # 오케스트레이션 모드
    mode: AgentMode = AgentMode.AGENTIC
    workflow_id: str | None = None
    hybrid_triggers: list[HybridTrigger] = field(default_factory=list)

    # 활성 도구
    tools: list[ToolRef] = field(default_factory=list)

    # 응답 설정
    system_prompt: str = ""
    response_policy: str = ResponsePolicy.BALANCED
    guardrails: list[str] = field(default_factory=list)

    # LLM 설정
    router_model: str = "haiku"
    main_model: str = "sonnet"

    # 메모리
    memory_type: str = "short"  # "short" | "session" | "long"
    memory_ttl_seconds: int = 3600

    # 에이전틱 모드 설정
    max_tool_calls: int = 5
    agent_timeout_seconds: int = 30

    # 커스텀 Intent
    intent_hints: list[IntentHint] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        """도구 이름 목록."""
        return [ref.name for ref in self.tools]
