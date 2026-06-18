"""Workflow Definition: 순차적 챗봇 워크플로우 정의 모델.

LLM 이전 시대의 결정 트리 챗봇 패턴을 재현한다.
Step 정의 → 분기 → 데이터 수집 → 결과 반환.

Step 타입:
    message:  봇이 메시지를 보낸다 (입력 불필요, 자동 진행)
    input:    봇이 질문하고, 사용자의 자유 텍스트 답변을 수집한다
    select:   봇이 선택지를 제시하고, 선택에 따라 분기한다
    confirm:  봇이 수집된 데이터를 요약하고, 사용자 확인을 받는다
    action:   외부 API/도구를 호출한다 (나중에 비즈니스별 구현)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkflowStep:
    """워크플로우 단계 정의."""

    id: str
    type: str  # "message" | "input" | "select" | "confirm" | "action" | "dynamic"
    prompt: str = ""
    system: str = ""  # dynamic 타입용 LLM 시스템 프롬프트(캐릭터 페르소나)
    save_as: str = ""  # 수집한 답변을 저장할 필드명
    options: list[str] = field(default_factory=list)  # select용 선택지
    branches: dict[str, str] = field(default_factory=dict)  # 선택지 → 다음 step id
    next: str | None = None  # 기본 다음 step (branches 없을 때)
    tool: str | None = None  # action용 도구명
    tool_params: dict = field(default_factory=dict)
    validation: str = ""  # 입력 검증 규칙 (예: "phone", "email", "number")
    # 이 스텝(또는 체인)에서 추천할 리포트 제품 CTA. 프론트가 버튼으로 렌더(깔때기 전환).
    # 값 예: "paper"(상세 리포트) | "compatibility"(궁합 리포트)
    report: str = ""

    # ── 신규(v2): 구조 신호 carry 메타 ──
    intent: str = ""               # confirm 스텝의 intent-config 키 (예: "compat")
    confirm_yes_label: str = ""    # 되묻기 yes 라벨 (기본 "응")
    confirm_no_label: str = ""     # 되묻기 no 라벨 (기본 "아니")
    collection_target: str = ""    # 수집 대상 (예: "partner") — collection 빌더가 사용
    collection_field: str = ""     # 이 input/select 스텝이 채우는 필드 key (예: "name"|"birthDate"|"birthTime"|"gender")
    collection_label: str = ""     # 필드 라벨 (예: "이름") — 없으면 collection_field 폴백

    # action step 전용 필드
    endpoint: str = ""  # 외부 API URL (빈 문자열이면 Profile 기본값 사용)
    http_method: str = "POST"  # HTTP 메서드
    headers_template: dict = field(default_factory=dict)  # {{field}} 치환 가능
    payload_template: dict = field(default_factory=dict)  # {{field}} 치환 가능
    timeout_seconds: int = 30  # HTTP 타임아웃
    on_success_message: str = ""  # 성공 시 봇 메시지
    on_error_message: str = ""  # 실패 시 봇 메시지


@dataclass(frozen=True)
class WorkflowDefinition:
    """워크플로우 정의."""

    id: str
    name: str
    description: str = ""
    first_step: str = ""  # 시작 step id (비어있으면 steps[0])
    steps: list[WorkflowStep] = field(default_factory=list)
    escape_policy: str = "allow"  # "allow" | "block" | "queue"
    max_retries: int = 3  # 같은 스텝에서 연속 검증 실패 시 자동 취소
    escape_keywords: list[str] = field(default_factory=list)  # 빈 리스트면 전역 _ESCAPE_KEYWORDS 사용

    def get_step(self, step_id: str) -> WorkflowStep | None:
        """step_id로 단계를 찾는다."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    @property
    def entry_step_id(self) -> str:
        """시작 step id."""
        return self.first_step or (self.steps[0].id if self.steps else "")
