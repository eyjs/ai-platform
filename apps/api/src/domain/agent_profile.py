"""AgentProfile: 도메인별 AI 에이전트 설정 모델.

ChatGPT의 GPTs 설정과 동일한 개념.
Agent는 하나(Universal Agent)이고, Profile만 바꾸면 행동이 바뀐다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.models import AgentMode, ResponsePolicy, SecurityLevel


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
    # LLM 의미 진입 분류용 설명. 키워드/intent fast-path가 빗나간 자유입력을
    # 이 설명 기준으로 의미 매칭(SemanticClassifier). 비면 workflow_id로 대체.
    description: str = ""


@dataclass(frozen=True)
class AgentProfile:
    """도메인별 AI 에이전트 설정.

    Profile과 Domain은 M:N 관계:
    - 하나의 Profile이 여러 Domain을 커버 가능
    - 하나의 Domain이 여러 Profile에서 참조 가능
    """

    id: str
    name: str
    description: str = ""

    # 도메인 스코프
    domain_scopes: list[str] = field(default_factory=list)
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
    memory_scopes: list[str] = field(default_factory=lambda: ["local"])
    memory_project_id: str | None = None
    memory_max_turns: int = 10
    memory_retention_days: int | None = None

    # 검증 넛지
    validation_nudge_enabled: bool = False
    validation_nudge_interval: int = 20
    validation_nudge_type: str = "fact_consistency"

    # 실행 경로
    execution_path: str = "subagent"

    # 에이전틱 모드 설정
    max_tool_calls: int = 5
    agent_timeout_seconds: int = 30
    llm_system_prefix: str | None = None  # None = 플랫폼 기본값 사용

    # Planner (Plan-and-Execute)
    planning_disabled: bool = False  # True이면 이 프로필에서 Planner 비활성화

    # 워크플로우 액션 기본값
    workflow_action_endpoint: str | None = None  # action step endpoint 기본값
    workflow_action_headers: dict = field(default_factory=dict)  # action step headers 기본값
    context_adapter: str | None = None  # dynamic 스텝 enrichment 어댑터 이름 (예: "saju")
    # 프롬프트 캐시 최소 크기 미달 시 채울 도메인 배경 텍스트(세션 안정). 비면 도메인 중립
    # 여백. agentic·workflow 양쪽 경로가 이 값을 filler로 공유한다(도메인 텍스트의 단일 출처).
    cache_padding_text: str = ""
    # 토큰이 비어 응답이 빈 채로 끝날 때 대신 발화할 폴백 문구(프로필 톤). None이면 범용 기본값.
    empty_response_fallback: str | None = None

    # 커스텀 Intent
    intent_hints: list[IntentHint] = field(default_factory=list)

    def __post_init__(self) -> None:
        """필드 간 교차 검증."""
        if "project" in self.memory_scopes and not self.memory_project_id:
            raise ValueError(
                "memory_project_id is required when 'project' in memory_scopes"
            )

    @property
    def tool_names(self) -> list[str]:
        """도구 이름 목록."""
        return [ref.name for ref in self.tools]
