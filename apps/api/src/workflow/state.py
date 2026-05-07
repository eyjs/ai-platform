"""Workflow Session State: 세션별 워크플로우 진행 상태.

"이 사용자가 지금 어떤 워크플로우의 몇 단계에 있고,
 지금까지 뭘 수집했는가"를 추적한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class WorkflowSession:
    """워크플로우 진행 상태."""

    workflow_id: str
    current_step_id: str
    collected: dict = field(default_factory=dict)  # save_as → 답변 값
    step_history: list = field(default_factory=list)  # 이전 스텝 ID 스택 (뒤로가기)
    started_at: float = field(default_factory=time.time)
    completed: bool = False
    retry_count: int = 0  # 현재 스텝 연속 실패 횟수
    awaiting_callback: bool = False  # 외부 API 응답 대기 중
    callback_response: dict = field(default_factory=dict)  # 외부 API 응답 데이터

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at
