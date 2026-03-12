"""ExecutionPlan + SearchScope: Router가 생성하는 실행 계획.

Router 4-Layer 처리 결과를 Agent/Workflow에 전달하는 데이터 모델.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class QuestionType(str, Enum):
    """질문 의도 분류 (8개 타입)."""

    GREETING = "GREETING"
    SYSTEM_META = "SYSTEM_META"
    ANSWER_REFERENCE = "ANSWER_REFERENCE"
    STANDALONE = "STANDALONE"
    SAME_DOC_FOLLOWUP = "SAME_DOC_FOLLOWUP"
    ANSWER_BASED_FOLLOWUP = "ANSWER_BASED_FOLLOWUP"
    CROSS_DOC_INTEGRATION = "CROSS_DOC_INTEGRATION"
    TOPIC_SWITCH = "TOPIC_SWITCH"


@dataclass(frozen=True)
class QuestionStrategy:
    """QuestionType별 실행 전략 매트릭스."""

    needs_rag: bool = True
    history_turns: int = 0
    boost_recent: bool = False
    max_vector_chunks: int = 5
    max_graph_chunks: int = 3


@dataclass(frozen=True)
class SearchScope:
    """도구 실행 시 자동 주입되는 검색 범위.

    Profile.domain_scopes + User.role_level로 생성.
    ScopedTool만 이 값을 받는다.
    """

    domain_codes: list[str] = field(default_factory=list)
    category_ids: list[str] | None = None
    security_level_max: str = "PUBLIC"
    allowed_doc_ids: list[str] | None = None


@dataclass
class ExecutionPlan:
    """Router가 생성하는 실행 계획. Agent/Workflow에 전달."""

    mode: str  # "agentic" | "workflow"
    scope: SearchScope
    tools: list = field(default_factory=list)
    system_prompt: str = ""
    guardrail_chain: list = field(default_factory=list)
    question_type: QuestionType = QuestionType.STANDALONE
    strategy: QuestionStrategy = field(default_factory=QuestionStrategy)
    workflow_step: str | None = None
    conversation_context: str = ""
