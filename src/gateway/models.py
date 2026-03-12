"""AI Gateway 요청/응답 모델.

Gateway는 인증, Profile 로딩, 요청 라우팅, SSE 스트리밍을 담당.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """챗봇 요청 모델."""

    question: str
    chatbot_id: str  # = profile_id
    session_id: str | None = None
    user_id: str | None = None
    user_role: str | None = None


class ChatResponse(BaseModel):
    """챗봇 응답 모델 (비스트리밍)."""

    answer: str
    sources: list[SourceRef] = []
    trace: TraceInfo | None = None


class SourceRef(BaseModel):
    """출처 참조."""

    document_id: str
    title: str
    chunk_text: str = ""
    score: float = 0.0
    method: str = ""  # "vector" | "fact" | "graph"


class TraceInfo(BaseModel):
    """AI 추론 과정 추적 정보."""

    question_type: str = ""
    mode: str = ""
    tools_called: list[str] = []
    router_decision: dict = {}
    latency_ms: float = 0.0


class IngestRequest(BaseModel):
    """문서 수집 요청."""

    title: str
    content: str | None = None
    source_url: str | None = None
    domain_code: str
    file_name: str | None = None
    security_level: str = "PUBLIC"
    metadata: dict = {}


class IngestResponse(BaseModel):
    """문서 수집 응답."""

    document_id: str
    chunks: int
    status: str


@dataclass
class UserContext:
    """인증 후 생성되는 사용자 맥락."""

    user_id: str = ""
    user_role: str = "VIEWER"
    security_level_max: str = "PUBLIC"
