"""ExecutionPlan: Router가 생성하는 실행 계획.

Router 4-Layer 처리 결과를 Agent/Workflow에 전달하는 데이터 모델.
SearchScope, AgentMode는 domain.models에 정의 (Tools/Agent 공유).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.domain.models import AgentMode, ResponsePolicy, SearchScope  # noqa: F401 (re-export)


class QuestionType(str, Enum):
    """질문 의도 분류."""

    GREETING = "GREETING"
    SYSTEM_META = "SYSTEM_META"
    STANDALONE = "STANDALONE"
    SAME_DOC_FOLLOWUP = "SAME_DOC_FOLLOWUP"
    ANSWER_BASED_FOLLOWUP = "ANSWER_BASED_FOLLOWUP"
    CROSS_DOC_INTEGRATION = "CROSS_DOC_INTEGRATION"


@dataclass(frozen=True)
class QuestionStrategy:
    """QuestionType별 실행 전략 매트릭스."""

    needs_rag: bool = True
    history_turns: int = 0
    max_vector_chunks: int = 5


@dataclass(frozen=True)
class ToolCall:
    """개별 도구 호출 단위. tool_name + params."""

    tool_name: str
    params: dict = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """Router가 생성하는 실행 계획. Agent/Workflow에 전달."""

    mode: AgentMode
    scope: SearchScope
    tool_groups: list[list[ToolCall]] = field(default_factory=list)
    system_prompt: str = ""
    guardrail_chain: list[str] = field(default_factory=list)
    question_type: QuestionType = QuestionType.STANDALONE
    strategy: QuestionStrategy = field(default_factory=QuestionStrategy)
    workflow_id: str | None = None
    workflow_step: str | None = None
    conversation_context: str = ""
    response_policy: str = ResponsePolicy.BALANCED
    max_tool_calls: int = 5
    agent_timeout_seconds: int = 30
    direct_answer: str | None = None  # Orchestrator 직접 응답 (인사/잡담)
