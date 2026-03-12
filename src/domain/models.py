"""Domain Models: 레이어 간 공유되는 핵심 도메인 모델.

Gateway/Router/Agent/Tools 모두 이 모듈에서 임포트한다.
의존성 방향: Gateway -> Domain <- Agent <- Tools
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field


class AgentMode(str, Enum):
    """오케스트레이션 모드."""

    AGENTIC = "agentic"
    WORKFLOW = "workflow"
    HYBRID = "hybrid"


# --- 보안 등급 ---

class SecurityLevel(str, Enum):
    """문서 보안 등급."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    SECRET = "SECRET"


SECURITY_HIERARCHY: dict[str, int] = {
    SecurityLevel.PUBLIC: 0,
    SecurityLevel.INTERNAL: 1,
    SecurityLevel.CONFIDENTIAL: 2,
    SecurityLevel.SECRET: 3,
}


# --- 사용자 역할 ---

class UserRole(str, Enum):
    """사용자 역할."""

    VIEWER = "VIEWER"
    EDITOR = "EDITOR"
    REVIEWER = "REVIEWER"
    APPROVER = "APPROVER"
    ADMIN = "ADMIN"


# --- 응답 정책 ---

class ResponsePolicy(str, Enum):
    """응답 정책."""

    STRICT = "strict"
    BALANCED = "balanced"


@dataclass(frozen=True)
class SearchScope:
    """도구 실행 시 자동 주입되는 검색 범위.

    Profile.domain_scopes + User.role_level로 생성.
    ScopedTool만 이 값을 받는다.
    """

    domain_codes: list[str] = field(default_factory=list)
    category_ids: list[str] | None = None
    security_level_max: str = SecurityLevel.PUBLIC
    allowed_doc_ids: list[str] | None = None


class SourceRef(BaseModel):
    """출처 참조."""

    document_id: str
    title: str
    chunk_text: str = ""
    score: float = 0.0
    method: str = ""


class TraceInfo(BaseModel):
    """AI 추론 과정 추적 정보."""

    request_id: str = ""
    question_type: str = ""
    mode: str = ""
    tools_called: list[str] = Field(default_factory=list)
    router_decision: dict = Field(default_factory=dict)
    latency_ms: float = 0.0


class AgentResponse(BaseModel):
    """Agent 실행 결과. Gateway가 이를 직접 반환하거나 래핑한다."""

    answer: str
    sources: list[SourceRef] = Field(default_factory=list)
    trace: TraceInfo | None = None
