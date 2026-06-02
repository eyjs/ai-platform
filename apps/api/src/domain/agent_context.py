"""AgentContext: 도구 실행 시 전달되는 대화 맥락."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.models import UserRole


@dataclass
class AgentContext:
    """도구 실행 시 전달되는 대화 맥락."""

    session_id: str = ""
    user_id: str = ""
    user_role: str = UserRole.VIEWER
    conversation_history: list = field(default_factory=list)
    prior_doc_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    tenant_id: str | None = None  # 테넌트 격리(A2). 4b에서 검색 범위로 전파
