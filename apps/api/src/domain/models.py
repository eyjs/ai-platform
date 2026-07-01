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

    DETERMINISTIC = "deterministic"  # StateGraph: 정해진 Tool 순서 실행
    AGENTIC = "agentic"              # create_react_agent: LLM이 Tool 자율 선택
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


# 역할 위계 (F19 도구 실행단 인가 등에서 최소 요구 역할 비교용).
# SECURITY_HIERARCHY와 동일한 패턴 — 숫자가 클수록 상위 권한.
ROLE_HIERARCHY: dict[str, int] = {
    UserRole.VIEWER: 0,
    UserRole.EDITOR: 1,
    UserRole.REVIEWER: 2,
    UserRole.APPROVER: 3,
    UserRole.ADMIN: 4,
}


# --- 응답 정책 ---

class ResponsePolicy(str, Enum):
    """응답 정책."""

    STRICT = "strict"
    BALANCED = "balanced"


COMMON_DOMAIN = "_common"

# holding 도메인: 업로드 즉시 임베딩됐으나 아직 배치(도메인 할당) 전인 문서의 domain_code.
# 검색에서 **항상 제외**된다(빈 domain_scopes 프로필이라도) — 임베딩됨·비노출 불변식.
# 배치가 오면 kms_sync 가 실제 상품도메인으로 재태깅하여 검색에 편입된다.
UNPLACED_DOMAIN = "__unplaced__"


def resolve_domain_hierarchy(
    domain_codes: list[str],
    include_common: bool = True,
) -> list[str]:
    """도메인 코드를 계층적으로 확장한다.

    "ga/contract" → ["ga/contract", "ga", "_common"]
    "camping-a/reservation" → ["camping-a/reservation", "camping-a", "_common"]

    '/' 구분자로 상위 도메인을 자동 포함하여,
    하위 프로필이 상위 도메인의 공통 문서에 접근할 수 있게 한다.
    """
    if not domain_codes:
        # 빈 리스트 = 전체 검색 (general-chat 등). 필터 없이 모든 문서 접근.
        return []

    resolved: set[str] = set(domain_codes)
    for code in domain_codes:
        parts = code.split("/")
        for i in range(1, len(parts)):
            resolved.add("/".join(parts[:i]))
    if include_common:
        resolved.add(COMMON_DOMAIN)
    return sorted(resolved)


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
    tenant_id: str | None = None  # 테넌트 격리(A2). 4b에서 검색 WHERE에 적용
    session_id: str | None = None  # 세션 스코프 격리(Step26). 세션 업로드 문서 검색 시에만 주입


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
    # Task 014: 응답 식별자 (Gateway 가 세팅). 피드백 연결용.
    response_id: str | None = None
    # Task 014: 내부 전달용 — faithfulness guardrail 이 산출한 수치 스코어.
    # Gateway finally 블록에서 RequestLogEntry.faithfulness_score 로 옮긴다.
    guardrail_score: float | None = None
