"""Workflow step 순수 로직: 컨텍스트 표시 필터·분기 결정·입력 검증.

엔진(WorkflowEngine)이 의존하는 부수효과 없는 헬퍼 모음. 세션/외부 의존 없이
step 정의 + collected/입력만으로 동작한다(테스트·재사용 용이).
"""

from __future__ import annotations

import re

from src.workflow.definition import WorkflowStep

# 입력 검증 패턴
_VALIDATORS: dict[str, re.Pattern] = {
    "phone": re.compile(r"^01[016789]-?\d{3,4}-?\d{4}$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "number": re.compile(r"^\d+$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
}


def _visible_ctx_lines(collected: dict) -> list[str]:
    """LLM 컨텍스트에 표시할 collected 항목을 'key: value' 라인으로 만든다.

    제외: `_`-prefix 내부 키, 범용 `session_id`, 그리고 어댑터가 등록한 식별자 키
    (`_hidden_keys`). 엔진은 도메인 식별자 이름(예: saju_id)을 직접 알지 않는다 —
    어댑터가 bind 시 자신의 식별자를 `_hidden_keys`에 등록한다.
    """
    hidden = {"session_id"} | set(collected.get("_hidden_keys") or [])
    return [
        f"- {k}: {v}"
        for k, v in collected.items()
        if not k.startswith("_") and k not in hidden
    ]


def _collection_steps(definition, target: str) -> list:
    """워크플로우 정의에서 collection_target이 일치하는 수집 스텝 목록을 순서대로 반환한다.

    엔진은 도메인을 알지 않는다 — yaml 메타(collection_field/target)에서 기계적으로 조립.
    """
    return [
        s for s in definition.steps
        if s.collection_field and s.collection_target == target
    ]


def _resolve_next(step: WorkflowStep, user_input: str) -> str | None:
    """사용자 입력에 따라 다음 스텝 ID를 결정한다."""
    if step.branches:
        # 정확한 매칭 시도
        if user_input in step.branches:
            return step.branches[user_input]
        # 대소문자 무시 매칭
        input_lower = user_input.strip().lower()
        for key, next_id in step.branches.items():
            if key.lower() == input_lower:
                return next_id
        # 번호 매칭 (1, 2, 3...)
        if user_input.strip().isdigit():
            idx = int(user_input.strip()) - 1
            keys = list(step.branches.keys())
            if 0 <= idx < len(keys):
                return step.branches[keys[idx]]
        # 부분문자열 매칭은 제거(오매칭·맥락 무시 원인) — 자유입력은 advance()에서
        # 공통 SemanticClassifier가 의미로 분류한다. 여기선 fallback next만 반환.
        return step.next
    return step.next


def _validate_input(step: WorkflowStep, user_input: str) -> str:
    """입력 검증. 실패 시 에러 메시지, 성공 시 빈 문자열."""
    if not step.validation:
        return ""

    # select 타입: options 중 하나여야 함
    if step.type == "select" and step.options:
        input_lower = user_input.strip().lower()
        # 정확 매칭
        if any(opt.lower() == input_lower for opt in step.options):
            return ""
        # 번호 매칭
        if user_input.strip().isdigit():
            idx = int(user_input.strip()) - 1
            if 0 <= idx < len(step.options):
                return ""
        options_str = ", ".join(f"{i+1}. {opt}" for i, opt in enumerate(step.options))
        return f"다음 중 하나를 선택해주세요:\n{options_str}"

    # 패턴 검증
    pattern = _VALIDATORS.get(step.validation)
    if pattern and not pattern.match(user_input.strip()):
        hints = {
            "phone": "전화번호 형식이 올바르지 않습니다. (예: 010-1234-5678)",
            "email": "이메일 형식이 올바르지 않습니다. (예: user@example.com)",
            "number": "숫자만 입력해주세요.",
            "date": "날짜 형식이 올바르지 않습니다. (예: 2026-03-13)",
        }
        return hints.get(step.validation, f"입력 형식이 올바르지 않습니다. ({step.validation})")
    return ""
