"""Workflow StepResult: 엔진이 스텝 처리 후 반환하는 결과 DTO."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepResult:
    """엔진이 반환하는 스텝 처리 결과."""

    bot_message: str
    options: list[str] = field(default_factory=list)  # select 타입일 때 선택지
    step_id: str = ""
    step_type: str = ""
    collected: dict = field(default_factory=dict)  # 지금까지 수집된 데이터
    completed: bool = False  # 워크플로우 종료 여부
    escaped: bool = False  # 사용자가 이탈(취소)했는지
    action_result: dict = field(default_factory=dict)  # action 타입 결과
    report: str = ""  # 워크플로우 YAML step.report에서 지정하는 CTA 키 (프론트 버튼용 힌트)
    # ── 구조신호 필드(YAML 메타에서 조립, 프론트/consumer가 소비) ──
    intent_confirm: dict = field(default_factory=dict)  # {intent, yes_label, no_label} — confirm-류 되묻기
    collection: dict = field(default_factory=dict)      # {target, fields[], parse_preview} — 수집 스텝 메타
    concluded: bool = False                             # 종료 명시 신호(completed와 정합)
