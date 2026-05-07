"""Workflow Engine: 순차적 챗봇 실행 엔진.

결정 트리 기반 대화를 실행한다.
엔진은 상태(WorkflowSession)를 받아서 현재 스텝을 처리하고,
다음 스텝으로 전이한 결과를 반환한다.

모든 공개 메서드는 async — 세션 영속화 + 외부 API 호출 지원.

사용법:
    engine = WorkflowEngine(store, session_store=session_store, action_client=action_client)
    result = await engine.start("insurance_contract", session_id)
    result = await engine.advance(session_id, user_input="자동차")
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from src.common.exceptions import GatewayError
from src.observability.logging import get_logger
from src.workflow.action_client import ActionClient, WorkflowActionError
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.session_store import WorkflowSessionStore
from src.workflow.state import WorkflowSession
from src.workflow.store import WorkflowStore
from src.workflow.template import render_template

logger = get_logger(__name__)

_MAX_MESSAGE_CHAIN = 10  # message 타입 연쇄 최대 깊이
_SESSION_TTL_SECONDS = 3600  # 세션 만료 시간 (1시간)

# 이탈(escape) 키워드 — escape_policy="allow"일 때 워크플로우 즉시 종료
_ESCAPE_KEYWORDS = {"취소", "처음으로", "나가기", "중단", "그만", "exit", "cancel", "quit"}

# 뒤로가기 키워드
_BACK_KEYWORDS = {"뒤로", "이전", "돌아가기", "back", "prev"}


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


# 입력 검증 패턴
_VALIDATORS: dict[str, re.Pattern] = {
    "phone": re.compile(r"^01[016789]-?\d{3,4}-?\d{4}$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "number": re.compile(r"^\d+$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
}


class WorkflowEngine:
    """순차적 챗봇 실행 엔진.

    세션 영속화: session_store가 주입되면 PostgreSQL에 저장,
    없으면 인메모리 dict 사용 (하위 호환).

    Action step: action_client가 주입되면 외부 HTTP 호출 가능,
    없으면 action step에서 에러 메시지 반환.
    """

    def __init__(
        self,
        store: WorkflowStore,
        session_store: WorkflowSessionStore | None = None,
        action_client: ActionClient | None = None,
    ) -> None:
        self._store = store
        self._session_store = session_store
        self._action_client = action_client
        # 인메모리 폴백 (session_store 미주입 시)
        self._sessions: dict[str, WorkflowSession] = {}

    async def _load_session(self, session_id: str) -> WorkflowSession | None:
        """세션을 로드한다. session_store 우선, 없으면 인메모리."""
        if self._session_store:
            return await self._session_store.load(session_id)
        return self._sessions.get(session_id)

    async def _save_session(self, session_id: str, session: WorkflowSession) -> None:
        """세션을 저장한다. session_store 우선, 없으면 인메모리."""
        if self._session_store:
            await self._session_store.save(session_id, session)
        else:
            self._sessions[session_id] = session

    async def _delete_session(self, session_id: str) -> None:
        """세션을 삭제한다."""
        if self._session_store:
            await self._session_store.delete(session_id)
        else:
            self._sessions.pop(session_id, None)

    async def start(
        self,
        workflow_id: str,
        session_id: str,
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
    ) -> StepResult:
        """워크플로우를 시작하고, 첫 번째 스텝의 봇 메시지를 반환한다.

        Args:
            workflow_id: 워크플로우 정의 ID
            session_id: 대화 세션 ID
            action_endpoint: Profile 기본 action 엔드포인트 (step에 미지정 시 사용)
            action_headers: Profile 기본 action 헤더 (step에 미지정 시 사용)
        """
        if not self._session_store:
            self._cleanup_expired_sessions()

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

        logger.info(
            "workflow_start",
            layer="WORKFLOW",
            workflow_id=workflow_id,
            session_id=session_id,
            first_step=entry_id,
        )

        result = await self._process_current_step(
            definition, session, session_id,
            action_endpoint=action_endpoint,
            action_headers=action_headers,
        )
        await self._save_session(session_id, session)
        return result

    async def advance(
        self,
        session_id: str,
        user_input: str,
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
    ) -> StepResult:
        """사용자 입력을 받아 다음 스텝으로 전이한다.

        Args:
            session_id: 대화 세션 ID
            user_input: 사용자 입력 텍스트
            action_endpoint: Profile 기본 action 엔드포인트
            action_headers: Profile 기본 action 헤더
        """
        session = await self._load_session(session_id)
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

        result = await self._advance_inner(
            session, session_id, definition, current_step, user_input,
            action_endpoint, action_headers,
        )
        await self._save_session(session_id, session)
        return result

    async def _advance_inner(
        self,
        session: WorkflowSession,
        session_id: str,
        definition: WorkflowDefinition,
        current_step: WorkflowStep,
        user_input: str,
        action_endpoint: str | None,
        action_headers: dict | None,
    ) -> StepResult:
        """advance 내부 로직. 세션 저장은 호출자가 담당한다."""
        # 이탈 감지 (escape_policy="allow"일 때만)
        escape_result = self._check_escape(user_input, session, definition)
        if escape_result:
            return escape_result

        # 뒤로가기 감지
        if user_input.strip().lower() in _BACK_KEYWORDS or any(
            kw in user_input for kw in _BACK_KEYWORDS
        ):
            if session.step_history:
                prev_step_id = session.step_history.pop()
                prev_step = definition.get_step(prev_step_id)
                if prev_step and prev_step.save_as and prev_step.save_as in session.collected:
                    del session.collected[prev_step.save_as]
                session.current_step_id = prev_step_id
                session.retry_count = 0
                logger.info(
                    "workflow_back",
                    layer="WORKFLOW",
                    session_id=session_id,
                    from_step=current_step.id,
                    to_step=prev_step_id,
                )
                return await self._process_current_step(
                    definition, session, session_id,
                    action_endpoint=action_endpoint,
                    action_headers=action_headers,
                )
            else:
                return StepResult(
                    bot_message="첫 번째 단계입니다. 더 이상 뒤로 갈 수 없습니다.",
                    options=current_step.options,
                    step_id=current_step.id,
                    step_type=current_step.type,
                    collected=dict(session.collected),
                )

        # 입력 검증
        validation_error = _validate_input(current_step, user_input)
        if validation_error:
            session.retry_count += 1
            if session.retry_count >= definition.max_retries:
                logger.info(
                    "workflow_retry_limit",
                    layer="WORKFLOW",
                    session_id=session_id,
                    step_id=current_step.id,
                    retries=session.retry_count,
                )
                session.completed = True
                return StepResult(
                    bot_message="입력이 지연되어 진행을 취소합니다. 다른 도움이 필요하시면 말씀해주세요.",
                    completed=True,
                    escaped=True,
                    collected=dict(session.collected),
                )
            return StepResult(
                bot_message=validation_error,
                options=current_step.options,
                step_id=current_step.id,
                step_type=current_step.type,
                collected=dict(session.collected),
            )

        # 검증 통과 -> retry 카운터 리셋
        session.retry_count = 0

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

        session.step_history.append(current_step.id)
        session.current_step_id = next_step_id
        return await self._process_current_step(
            definition, session, session_id,
            action_endpoint=action_endpoint,
            action_headers=action_headers,
        )

    async def get_session(self, session_id: str) -> Optional[WorkflowSession]:
        """세션 상태를 조회한다."""
        return await self._load_session(session_id)

    async def cancel(self, session_id: str) -> bool:
        """워크플로우를 취소한다."""
        session = await self._load_session(session_id)
        if session:
            await self._delete_session(session_id)
            logger.info("workflow_cancel", layer="WORKFLOW", session_id=session_id)
            return True
        return False

    async def resume(
        self,
        workflow_id: str,
        session_id: str,
        step_id: str,
        collected: dict,
    ) -> StepResult:
        """일시 중지된 워크플로우를 재개한다."""
        definition = self._store.get(workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우를 찾을 수 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_NOT_FOUND",
            )

        step = definition.get_step(step_id)
        if not step:
            raise GatewayError(
                f"스텝을 찾을 수 없습니다: {step_id}",
                error_code="ERR_WORKFLOW_STEP_MISSING",
            )

        session = WorkflowSession(
            workflow_id=workflow_id,
            current_step_id=step_id,
            collected=dict(collected),
        )

        logger.info(
            "workflow_resume",
            layer="WORKFLOW",
            workflow_id=workflow_id,
            session_id=session_id,
            step_id=step_id,
            collected_keys=list(collected.keys()),
        )

        result = await self._process_current_step(definition, session, session_id)
        await self._save_session(session_id, session)
        return result

    def _check_escape(
        self,
        user_input: str,
        session: WorkflowSession,
        definition: WorkflowDefinition,
    ) -> Optional[StepResult]:
        """이탈 키워드를 감지한다. escape_policy에 따라 처리.

        워크플로우별 escape_keywords가 정의되어 있으면 우선 사용하고,
        없으면 전역 _ESCAPE_KEYWORDS를 사용한다.
        """
        if definition.escape_policy != "allow":
            return None

        normalized = user_input.strip().lower()

        # 워크플로우별 escape_keywords 우선, 없으면 전역 폴백
        keywords = (
            {kw.lower() for kw in definition.escape_keywords}
            if definition.escape_keywords
            else _ESCAPE_KEYWORDS
        )

        if normalized not in keywords:
            return None

        # 워크플로우 취소
        logger.info(
            "workflow_escape",
            layer="WORKFLOW",
            workflow_id=session.workflow_id,
            trigger=normalized,
            collected_keys=list(session.collected.keys()),
        )
        session.completed = True
        return StepResult(
            bot_message="워크플로우가 취소되었습니다. 다른 질문이 있으시면 말씀해주세요.",
            completed=True,
            escaped=True,
            collected=dict(session.collected),
        )

    def _cleanup_expired_sessions(self) -> None:
        """만료된 인메모리 세션을 정리한다 (session_store 미사용 시)."""
        now = time.time()
        expired = [
            sid for sid, session in self._sessions.items()
            if now - session.started_at > _SESSION_TTL_SECONDS
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info("workflow_sessions_cleaned", count=len(expired))

    async def _process_current_step(
        self,
        definition: WorkflowDefinition,
        session: WorkflowSession,
        session_id: str = "",
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
    ) -> StepResult:
        """현재 스텝을 처리하고 StepResult를 반환한다.

        message 타입은 자동으로 다음 스텝으로 체이닝된다.
        action 타입은 외부 API를 호출하고 결과에 따라 다음 스텝으로 전이한다.
        무한 루프 방지를 위해 _MAX_MESSAGE_CHAIN 깊이 제한을 적용한다.
        """
        message_parts: list[str] = []

        for _ in range(_MAX_MESSAGE_CHAIN):
            step = definition.get_step(session.current_step_id)
            if not step:
                session.completed = True
                return StepResult(bot_message="스텝 오류", completed=True)

            rendered = render_template(step.prompt, session.collected)

            # action 타입: 외부 API 호출 후 자동 진행
            if step.type == "action":
                action_result = await self._execute_action_step(
                    step, session, action_endpoint, action_headers,
                )
                if action_result.completed or not step.next:
                    # 액션 실패 또는 다음 스텝 없음 -> 종료
                    if message_parts:
                        action_result = StepResult(
                            bot_message="\n\n".join(message_parts) + "\n\n" + action_result.bot_message,
                            options=action_result.options,
                            step_id=action_result.step_id,
                            step_type=action_result.step_type,
                            collected=action_result.collected,
                            completed=action_result.completed,
                            action_result=action_result.action_result,
                        )
                    return action_result

                # 액션 성공 + 다음 스텝 있음 -> 메시지 축적 후 다음 스텝으로
                if action_result.bot_message:
                    message_parts.append(action_result.bot_message)
                session.current_step_id = step.next
                continue

            # message 이외 타입: 메시지 축적 후 반환
            if step.type != "message":
                if step.type == "confirm":
                    summary_lines = [f"- {k}: {v}" for k, v in session.collected.items()]
                    summary = "\n".join(summary_lines)
                    rendered = f"{rendered}\n\n{summary}"

                message_parts.append(rendered)
                return StepResult(
                    bot_message="\n\n".join(message_parts),
                    options=list(step.options),
                    step_id=step.id,
                    step_type=step.type,
                    collected=dict(session.collected),
                )

            # message 타입: 축적하고 다음 스텝으로 자동 진행
            message_parts.append(rendered)
            if not step.next or not definition.get_step(step.next):
                session.completed = True
                return StepResult(
                    bot_message="\n\n".join(message_parts),
                    completed=True,
                    collected=dict(session.collected),
                    step_id=step.id,
                    step_type=step.type,
                )
            session.current_step_id = step.next

        # 깊이 제한 도달
        logger.warning(
            "workflow_message_chain_limit",
            layer="WORKFLOW",
            session_id=session.workflow_id,
            depth=_MAX_MESSAGE_CHAIN,
        )
        session.completed = True
        return StepResult(
            bot_message="\n\n".join(message_parts),
            completed=True,
            collected=dict(session.collected),
        )

    async def _execute_action_step(
        self,
        step: WorkflowStep,
        session: WorkflowSession,
        profile_endpoint: str | None = None,
        profile_headers: dict | None = None,
    ) -> StepResult:
        """action step을 실행한다.

        1. endpoint: step.endpoint > profile_endpoint (둘 다 없으면 에러)
        2. headers: step.headers_template + profile_headers 병합
        3. payload: step.payload_template
        4. 호출 성공 -> on_success_message + 다음 스텝 진행
        5. 호출 실패 -> on_error_message + 워크플로우 종료
        """
        if not self._action_client:
            logger.error(
                "action_step_no_client",
                layer="WORKFLOW",
                step_id=step.id,
            )
            return StepResult(
                bot_message=step.on_error_message or "외부 연동 기능이 비활성화되어 있습니다.",
                step_id=step.id,
                step_type="action",
                collected=dict(session.collected),
                completed=True,
            )

        # 엔드포인트 결정: step > profile
        endpoint = step.endpoint or profile_endpoint
        if not endpoint:
            logger.error(
                "action_step_no_endpoint",
                layer="WORKFLOW",
                step_id=step.id,
            )
            return StepResult(
                bot_message=step.on_error_message or "외부 API 엔드포인트가 설정되지 않았습니다.",
                step_id=step.id,
                step_type="action",
                collected=dict(session.collected),
                completed=True,
            )

        # 헤더 병합: profile 기본값 + step 오버라이드
        merged_headers = dict(profile_headers or {})
        if step.headers_template:
            merged_headers.update(step.headers_template)

        try:
            response_data = await self._action_client.call(
                endpoint=endpoint,
                method=step.http_method,
                headers=merged_headers if merged_headers else None,
                payload=step.payload_template if step.payload_template else None,
                timeout=step.timeout_seconds,
                collected=session.collected,
            )

            # 응답 데이터를 세션에 저장 (save_as가 있으면)
            if step.save_as:
                session.collected[step.save_as] = response_data

            # 콜백 응답도 세션에 기록
            session.callback_response = response_data

            success_message = render_template(
                step.on_success_message or "처리가 완료되었습니다.",
                session.collected,
            )

            logger.info(
                "action_step_success",
                layer="WORKFLOW",
                step_id=step.id,
                endpoint=endpoint[:100],
            )

            return StepResult(
                bot_message=success_message,
                step_id=step.id,
                step_type="action",
                collected=dict(session.collected),
                action_result=response_data,
            )

        except WorkflowActionError as e:
            logger.warning(
                "action_step_failed",
                layer="WORKFLOW",
                step_id=step.id,
                endpoint=endpoint[:100],
                status_code=e.status_code,
                error=str(e),
            )

            error_message = render_template(
                step.on_error_message or "외부 시스템 연동 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                session.collected,
            )

            return StepResult(
                bot_message=error_message,
                step_id=step.id,
                step_type="action",
                collected=dict(session.collected),
                completed=True,
                action_result={"error": str(e), "status_code": e.status_code},
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
