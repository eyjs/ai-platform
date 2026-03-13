"""Workflow Engine: 순차적 챗봇 실행 엔진.

결정 트리 기반 대화를 실행한다.
엔진은 상태(WorkflowSession)를 받아서 현재 스텝을 처리하고,
다음 스텝으로 전이한 결과를 반환한다.

사용법:
    engine = WorkflowEngine(store)
    result = engine.start("insurance_contract", session_id)
    # → StepResult(bot_message="어떤 보험에 가입하시겠어요?", options=[...])

    result = engine.advance(session_id, user_input="자동차")
    # → StepResult(bot_message="차량 연식이 어떻게 되나요?", ...)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from src.common.exceptions import GatewayError
from src.observability.logging import get_logger
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.state import WorkflowSession
from src.workflow.store import WorkflowStore

logger = get_logger(__name__)


@dataclass
class StepResult:
    """엔진이 반환하는 스텝 처리 결과."""

    bot_message: str
    options: list[str] = field(default_factory=list)  # select 타입일 때 선택지
    step_id: str = ""
    step_type: str = ""
    collected: dict = field(default_factory=dict)  # 지금까지 수집된 데이터
    completed: bool = False  # 워크플로우 종료 여부
    action_result: dict = field(default_factory=dict)  # action 타입 결과


# 입력 검증 패턴
_VALIDATORS: dict[str, re.Pattern] = {
    "phone": re.compile(r"^01[016789]-?\d{3,4}-?\d{4}$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "number": re.compile(r"^\d+$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
}


class WorkflowEngine:
    """순차적 챗봇 실행 엔진.

    세션 상태는 외부(SessionMemory)에서 관리하고,
    엔진은 순수하게 상태를 받아서 다음 상태를 반환한다.
    """

    def __init__(self, store: WorkflowStore) -> None:
        self._store = store
        self._sessions: dict[str, WorkflowSession] = {}

    def start(self, workflow_id: str, session_id: str) -> StepResult:
        """워크플로우를 시작하고, 첫 번째 스텝의 봇 메시지를 반환한다."""
        definition = self._store.get(workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우를 찾을 수 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_NOT_FOUND",
            )

        if not definition.steps:
            raise GatewayError(
                f"워크플로우에 스텝이 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_EMPTY",
            )

        entry_id = definition.entry_step_id
        session = WorkflowSession(
            workflow_id=workflow_id,
            current_step_id=entry_id,
        )
        self._sessions[session_id] = session

        logger.info(
            "workflow_start",
            layer="WORKFLOW",
            workflow_id=workflow_id,
            session_id=session_id,
            first_step=entry_id,
        )

        return self._process_current_step(definition, session)

    def advance(self, session_id: str, user_input: str) -> StepResult:
        """사용자 입력을 받아 다음 스텝으로 전이한다."""
        session = self._sessions.get(session_id)
        if not session:
            raise GatewayError(
                "활성 워크플로우 세션이 없습니다",
                error_code="ERR_WORKFLOW_NO_SESSION",
            )

        if session.completed:
            return StepResult(
                bot_message="이미 완료된 워크플로우입니다.",
                completed=True,
                collected=dict(session.collected),
            )

        definition = self._store.get(session.workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우 정의가 사라졌습니다: {session.workflow_id}",
                error_code="ERR_WORKFLOW_MISSING",
            )

        current_step = definition.get_step(session.current_step_id)
        if not current_step:
            raise GatewayError(
                f"스텝을 찾을 수 없습니다: {session.current_step_id}",
                error_code="ERR_WORKFLOW_STEP_MISSING",
            )

        # 입력 검증
        validation_error = _validate_input(current_step, user_input)
        if validation_error:
            return StepResult(
                bot_message=validation_error,
                options=current_step.options,
                step_id=current_step.id,
                step_type=current_step.type,
                collected=dict(session.collected),
            )

        # 데이터 수집
        if current_step.save_as:
            session.collected[current_step.save_as] = user_input

        # 다음 스텝 결정
        next_step_id = _resolve_next(current_step, user_input)

        logger.info(
            "workflow_advance",
            layer="WORKFLOW",
            session_id=session_id,
            from_step=current_step.id,
            to_step=next_step_id or "END",
            user_input=user_input[:50],
        )

        # 종료
        if not next_step_id:
            session.completed = True
            return StepResult(
                bot_message="워크플로우가 완료되었습니다.",
                completed=True,
                collected=dict(session.collected),
                step_id=current_step.id,
                step_type="complete",
            )

        next_step = definition.get_step(next_step_id)
        if not next_step:
            session.completed = True
            return StepResult(
                bot_message=f"다음 스텝({next_step_id})을 찾을 수 없습니다.",
                completed=True,
                collected=dict(session.collected),
            )

        session.current_step_id = next_step_id
        return self._process_current_step(definition, session)

    def get_session(self, session_id: str) -> Optional[WorkflowSession]:
        """세션 상태를 조회한다."""
        return self._sessions.get(session_id)

    def cancel(self, session_id: str) -> bool:
        """워크플로우를 취소한다."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info("workflow_cancel", layer="WORKFLOW", session_id=session_id)
            return True
        return False

    def _process_current_step(
        self,
        definition: WorkflowDefinition,
        session: WorkflowSession,
    ) -> StepResult:
        """현재 스텝을 처리하고 StepResult를 반환한다."""
        step = definition.get_step(session.current_step_id)
        if not step:
            session.completed = True
            return StepResult(bot_message="스텝 오류", completed=True)

        # message 타입: 봇이 메시지를 보내고 자동으로 다음 스텝으로 진행
        if step.type == "message":
            bot_message = _render_template(step.prompt, session.collected)
            if step.next:
                next_step = definition.get_step(step.next)
                if next_step:
                    session.current_step_id = step.next
                    # 다음 스텝도 message면 연쇄 처리
                    next_result = self._process_current_step(definition, session)
                    return StepResult(
                        bot_message=bot_message + "\n\n" + next_result.bot_message,
                        options=next_result.options,
                        step_id=next_result.step_id,
                        step_type=next_result.step_type,
                        collected=next_result.collected,
                        completed=next_result.completed,
                    )
            # next가 없으면 종료
            session.completed = True
            return StepResult(
                bot_message=bot_message,
                completed=True,
                collected=dict(session.collected),
                step_id=step.id,
                step_type=step.type,
            )

        bot_message = _render_template(step.prompt, session.collected)

        # confirm 타입: 수집된 데이터 요약을 봇 메시지에 포함
        if step.type == "confirm":
            summary_lines = [f"- {k}: {v}" for k, v in session.collected.items()]
            summary = "\n".join(summary_lines)
            bot_message = f"{bot_message}\n\n{summary}"

        return StepResult(
            bot_message=bot_message,
            options=list(step.options),
            step_id=step.id,
            step_type=step.type,
            collected=dict(session.collected),
        )


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
        # 부분 매칭
        for key, next_id in step.branches.items():
            if key in user_input or user_input in key:
                return next_id
        # 매칭 실패 시 기본 next
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


def _render_template(template: str, data: dict) -> str:
    """수집된 데이터를 템플릿에 대입한다.

    예: "{{name}}님, 연락처를 알려주세요." → "홍길동님, 연락처를 알려주세요."
    """
    result = template
    for key, value in data.items():
        result = result.replace("{{" + key + "}}", str(value))
    return result
