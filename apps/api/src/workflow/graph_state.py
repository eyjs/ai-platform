"""Workflow Graph State: LangGraph StateGraph에 사용하는 TypedDict 상태 채널 정의.

WorkflowSession(state.py)의 인메모리 표현을 LangGraph checkpointer 친화적인
TypedDict 채널로 매핑한다. 채널 reducer는 대부분 last-write-wins(기본값).
message_parts만 노드 내부에서 누적 후 통째 교체한다(자동 전이 체인 동안 carry).
"""

from __future__ import annotations

import time
from typing import TypedDict


class WorkflowGraphState(TypedDict):
    """LangGraph StateGraph 채널.

    채널 네이밍은 WorkflowSession 필드와 1:1 대응한다.
    checkpointer(InMemorySaver 또는 PostgreSQL)가 이 dict를 직렬화/복원한다.
    """

    # 워크플로우 식별자
    workflow_id: str

    # 현재 실행 중인 step id
    current_step_id: str

    # save_as → 수집된 답변 (last-write-wins)
    collected: dict

    # 뒤로가기용 step id 스택 (last-write-wins — 노드가 새 list를 통째 반환)
    step_history: list

    # 현재 스텝 연속 검증 실패 횟수
    retry_count: int

    # 외부 API 응답 대기 플래그 (action step)
    awaiting_callback: bool

    # 외부 API 응답 데이터 (action step)
    callback_response: dict

    # 워크플로우 완료 여부
    completed: bool

    # 워크플로우 시작 타임스탬프 (UNIX epoch)
    started_at: float

    # message/dynamic/action 자동 전이 중 누적된 봇 메시지 조각 목록
    # (체인 중엔 통째 교체; 마지막 interrupt/END에서 join하여 last_result에 실림)
    message_parts: list

    # 현재 체인에서 만난 report CTA 힌트 (예: "paper", "compatibility")
    report_hint: str

    # 마지막 step 처리 결과를 dict로 직렬화한 값 (StepResult 필드 동등)
    # interrupt payload 및 Engine 인터페이스 호출자가 소비한다
    last_result: dict


def make_initial_state(workflow_id: str, entry_step_id: str) -> WorkflowGraphState:
    """워크플로우 시작 시 초기 상태를 생성한다."""
    return WorkflowGraphState(
        workflow_id=workflow_id,
        current_step_id=entry_step_id,
        collected={},
        step_history=[],
        retry_count=0,
        awaiting_callback=False,
        callback_response={},
        completed=False,
        started_at=time.time(),
        message_parts=[],
        report_hint="",
        last_result={},
    )
