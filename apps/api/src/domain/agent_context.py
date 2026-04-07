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
