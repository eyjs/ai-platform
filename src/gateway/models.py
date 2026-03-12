"""AI Gateway 요청/응답 모델.

Gateway 전용 DTO: 요청(ChatRequest, IngestRequest), 응답(IngestResponse), 인증(UserContext).
공유 도메인 모델(AgentResponse, SourceRef, TraceInfo, SearchScope)은 domain.models에 정의.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from src.domain.models import (  # noqa: F401 (re-export)
    AgentResponse, SecurityLevel, SourceRef, TraceInfo, UserRole,
)

MAX_QUESTION_LENGTH = 5000


class ChatRequest(BaseModel):
    """챗봇 요청 모델."""

    question: str
    chatbot_id: str  # = profile_id
    session_id: str | None = None

    @field_validator("question")
    @classmethod
    def question_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("question must not be empty")
        if len(v) > MAX_QUESTION_LENGTH:
            raise ValueError(f"question must be {MAX_QUESTION_LENGTH} characters or less")
        return v


class IngestRequest(BaseModel):
    """문서 수집 요청."""

    title: str
    content: str | None = None
    source_url: str | None = None
    domain_code: str
    file_name: str | None = None
    security_level: str = SecurityLevel.PUBLIC
    metadata: dict = Field(default_factory=dict)


class IngestResponse(BaseModel):
    """문서 수집 응답."""

    document_id: str
    chunks: int
    status: str


@dataclass
class UserContext:
    """인증 후 생성되는 사용자 맥락."""

    user_id: str = ""
    user_role: str = UserRole.VIEWER
    security_level_max: str = SecurityLevel.PUBLIC
    allowed_profiles: list[str] = field(default_factory=list)
    allowed_origins: list[str] = field(default_factory=list)
    rate_limit_per_min: int = 60
