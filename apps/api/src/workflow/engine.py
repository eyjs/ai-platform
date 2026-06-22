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

import time
from typing import Optional

from src.common.exceptions import GatewayError
from src.domain.classifier import Candidate
from src.observability.logging import get_logger
from src.workflow.action_client import ActionClient
from src.workflow.context_adapter import WorkflowContextAdapter
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.session_store import WorkflowSessionStore
from src.workflow.state import WorkflowSession
from src.workflow.step_executors import execute_action_step, generate_dynamic
from src.workflow.step_result import StepResult
from src.workflow.store import WorkflowStore
from src.workflow.template import render_template
# step 순수 로직(re-export 포함 — 기존 `from src.workflow.engine import _resolve_next` 등 호환)
from src.workflow.step_logic import (
    _collection_steps,
    _resolve_next,
    _validate_input,
    _visible_ctx_lines,
)

logger = get_logger(__name__)

_MAX_MESSAGE_CHAIN = 10  # message 타입 연쇄 최대 깊이
_SESSION_TTL_SECONDS = 3600  # 세션 만료 시간 (1시간)

# 이탈(escape) 키워드 — escape_policy="allow"일 때 워크플로우 즉시 종료
_ESCAPE_KEYWORDS = {"취소", "처음으로", "나가기", "중단", "그만", "exit", "cancel", "quit"}

# 뒤로가기 키워드
_BACK_KEYWORDS = {"뒤로", "이전", "돌아가기", "back", "prev"}

__all__ = ["WorkflowEngine", "StepResult"]


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
        llm=None,
        context_adapters: dict[str, WorkflowContextAdapter] | None = None,
        classifier=None,
    ) -> None:
        self._store = store
        self._session_store = session_store
        self._action_client = action_client
        # dynamic 스텝(LLM 캐릭터 통찰)용 LLMProvider. 없으면 dynamic은 정적 폴백.
        self._llm = llm
        # 서비스별 컨텍스트 enrichment 플러그인 (이름 → 어댑터). 프로파일이 선택한다.
        self._context_adapters = context_adapters or {}
        # select 분기 의미 분류용 공통 SemanticClassifier. 없으면 키워드 매칭만(하위호환).
        self._classifier = classifier
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
        context_adapter: str | None = None,
        cache_padding_text: str = "",
    ) -> StepResult:
        """워크플로우를 시작하고, 첫 번째 스텝의 봇 메시지를 반환한다.

        Args:
            workflow_id: 워크플로우 정의 ID
            session_id: 대화 세션 ID
            action_endpoint: Profile 기본 action 엔드포인트 (step에 미지정 시 사용)
            action_headers: Profile 기본 action 헤더 (step에 미지정 시 사용)
            cache_padding_text: 캐시 패딩용 도메인 배경 텍스트 (Profile이 지정). dynamic 스텝
                cacheable_system 패딩에 쓰인다. 비면 도메인 중립 여백.
            context_adapter: dynamic 스텝 enrichment에 쓸 어댑터 이름 (Profile이 지정).
                세션에 바인딩되어 이후 advance/dynamic 스텝에서 재사용된다.
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

        # 세션 컨텍스트를 워크플로우 변수로 주입한다(범용 — session_id만).
        # action 엔드포인트/템플릿에서 {{session_id}}로 참조 가능.
        session.collected["session_id"] = session_id

        # 캐시 패딩 도메인 텍스트를 세션에 바인딩(영속/복원됨). dynamic 스텝에서 filler로 사용.
        if cache_padding_text:
            session.collected["_pad_text"] = cache_padding_text

        # dynamic 스텝 enrichment 어댑터를 세션에 바인딩 (collected에 저장 → 영속/복원됨).
        if context_adapter:
            session.collected["_adapter"] = context_adapter
            # 도메인 식별자 추출은 어댑터가 소유한다(예: "saju-{uuid}-{product}" → saju_id).
            # 엔진은 도메인별 session_id 규약을 모른다.
            adapter = self._context_adapters.get(context_adapter)
            bind = getattr(adapter, "bind", None)
            if callable(bind):
                bind(session_id, session.collected)

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
                concluded=True,
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
                    concluded=True,
                    collected=dict(session.collected),
                )
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

        # 다음 스텝 결정 (exact/소문자/번호 — 버튼·명시 입력)
        next_step_id = _resolve_next(current_step, user_input)

        # 못 잡은 자유입력 → 공통 의미 분류기로 분기(맥락 기반, 키워드 아님).
        # 버튼·번호는 위에서 이미 잡히므로 여기 도달 시에만 LLM 호출(지연·비용 가드).
        if not next_step_id and current_step.branches and self._classifier:
            candidates = [Candidate(label=k) for k in current_step.branches]
            ctx = render_template(current_step.prompt, session.collected)
            ctx_lines = _visible_ctx_lines(session.collected)
            if ctx_lines:
                ctx = f"{ctx}\n[지금까지 파악된 정보]\n" + "\n".join(ctx_lines)
            decision = await self._classifier.classify(
                user_input, candidates, context=ctx,
            )
            if decision.label and decision.label in current_step.branches:
                next_step_id = current_step.branches[decision.label]
                if current_step.save_as:
                    # 원시 자유입력 대신 정규 분기키 저장(다운스트림 dynamic 스텝이 깔끔하게 사용)
                    session.collected[current_step.save_as] = decision.label
                logger.info(
                    "workflow_branch_llm_classified",
                    layer="WORKFLOW", session_id=session_id,
                    step_id=current_step.id, label=decision.label,
                    confidence=decision.confidence,
                )

        # select/branch 스텝에서 입력이 어떤 분기에도 안 맞고 fallback next도 없으면,
        # 워크플로우를 종료하지 말고(자유텍스트 조기종료 버그) 같은 스텝을 다시 안내한다.
        # 가이드형 funnel에서 버튼 대신 자유텍스트를 친 경우의 이탈 방어.
        # (retry_count 리셋은 정상 진행 확정 뒤로 미뤘으므로 미매칭이 누적된다)
        if not next_step_id and current_step.branches:
            # 방금 save_as에 잘못 담긴 미매칭 입력 롤백
            if current_step.save_as and current_step.save_as in session.collected:
                del session.collected[current_step.save_as]
            session.retry_count += 1
            if session.retry_count >= definition.max_retries:
                session.completed = True
                logger.info(
                    "workflow_select_no_match_escape",
                    layer="WORKFLOW", session_id=session_id,
                    step_id=current_step.id, retries=session.retry_count,
                )
                return StepResult(
                    bot_message="여러 번 이해하지 못했어요. 잠시 후 다시 시도해 주세요.",
                    completed=True,
                    escaped=True,
                    concluded=True,
                    collected=dict(session.collected),
                    step_id=current_step.id,
                    step_type=current_step.type,
                )
            logger.info(
                "workflow_select_no_match_reprompt",
                layer="WORKFLOW", session_id=session_id,
                step_id=current_step.id, retries=session.retry_count,
                user_input=user_input[:50],
            )
            # current_step_id 변경 없이 같은 스텝을 다시 안내(스텝 고유 프롬프트+옵션 재노출)
            return await self._process_current_step(
                definition, session, session_id,
                action_endpoint=action_endpoint,
                action_headers=action_headers,
            )

        # 정상 진행 확정 -> retry 카운터 리셋
        session.retry_count = 0

        logger.info(
            "workflow_advance",
            layer="WORKFLOW",
            session_id=session_id,
            from_step=current_step.id,
            to_step=next_step_id or "END",
            user_input=user_input[:50],
        )

        # 종료 (정상 종착: next도 branches도 없는 말단 스텝)
        if not next_step_id:
            session.completed = True
            return StepResult(
                bot_message="워크플로우가 완료되었습니다.",
                completed=True,
                concluded=True,
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
                concluded=True,
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
            concluded=True,
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
        # 메시지 체인(dynamic→message 등)에서 만난 report 힌트를 누적해 최종 결과에 싣는다.
        report_hint = ""

        for _ in range(_MAX_MESSAGE_CHAIN):
            step = definition.get_step(session.current_step_id)
            if not step:
                session.completed = True
                return StepResult(bot_message="스텝 오류", completed=True, concluded=True)

            if step.report:
                report_hint = step.report

            rendered = render_template(step.prompt, session.collected)

            # dynamic 타입: LLM이 collected 컨텍스트로 캐릭터 통찰을 생성 → message처럼 자동 진행
            if step.type == "dynamic":
                insight = await generate_dynamic(
                    step, session.collected,
                    llm=self._llm, context_adapters=self._context_adapters,
                )
                if insight:
                    message_parts.append(insight)
                if not step.next or not definition.get_step(step.next):
                    session.completed = True
                    return StepResult(
                        bot_message="\n\n".join(message_parts),
                        completed=True,
                        concluded=True,
                        collected=dict(session.collected),
                        step_id=step.id,
                        step_type=step.type,
                        report=report_hint,
                    )
                session.current_step_id = step.next
                continue

            # action 타입: 외부 API 호출 후 자동 진행
            if step.type == "action":
                action_result = await execute_action_step(
                    step, session, self._action_client, action_endpoint, action_headers,
                )
                if action_result.completed or not step.next:
                    # 액션 실패 또는 다음 스텝 없음 -> 워크플로우 종료.
                    # 종료 상태를 세션에 명시 저장해야 다음 메시지가 일반 대화로
                    # 복귀한다 (미설정 시 워크플로우에 갇힘).
                    session.completed = True
                    await self._save_session(session_id, session)
                    bot_message = action_result.bot_message
                    if message_parts:
                        bot_message = "\n\n".join(message_parts) + "\n\n" + bot_message
                    return StepResult(
                        bot_message=bot_message,
                        options=action_result.options,
                        step_id=action_result.step_id,
                        step_type=action_result.step_type,
                        collected=action_result.collected,
                        completed=True,
                        concluded=True,
                        action_result=action_result.action_result,
                        report=report_hint,
                    )

                # 액션 성공 + 다음 스텝 있음 -> 메시지 축적 후 다음 스텝으로
                if action_result.bot_message:
                    message_parts.append(action_result.bot_message)
                session.current_step_id = step.next
                continue

            # message 이외 타입: 메시지 축적 후 반환
            if step.type != "message":
                # ── confirm: 수집 요약 추가 + intent_confirm 구성 ──
                intent_confirm_meta: dict = {}
                if step.type == "confirm":
                    summary_lines = [f"- {k}: {v}" for k, v in session.collected.items()]
                    rendered = f"{rendered}\n\n" + "\n".join(summary_lines)
                    intent_confirm_meta = {
                        "intent": step.intent or "",
                        "yes_label": step.confirm_yes_label or "응",
                        "no_label": step.confirm_no_label or "아니",
                    }

                # ── input/select 수집 스텝: collection 구성 ──
                collection_meta: dict = {}
                if step.collection_field:
                    collection_steps = _collection_steps(definition, step.collection_target)
                    if collection_steps:
                        fields = []
                        for cs in collection_steps:
                            collected_value = session.collected.get(cs.save_as)
                            fields.append({
                                "key": cs.collection_field,
                                "label": cs.collection_label or cs.collection_field,
                                "value": collected_value,
                                "status": "filled" if collected_value not in (None, "") else "pending",
                            })
                        collection_meta = {
                            "target": step.collection_target or "partner",
                            "fields": fields,
                            "parse_preview": None,  # 골격: 정규화는 백엔드 범위
                        }
                    else:
                        # graceful fallback: 현 스텝의 단일 필드만 emit
                        collected_value = session.collected.get(step.save_as)
                        collection_meta = {
                            "target": step.collection_target or "partner",
                            "fields": [{
                                "key": step.collection_field,
                                "label": step.collection_label or step.collection_field,
                                "value": collected_value,
                                "status": "filled" if collected_value not in (None, "") else "pending",
                            }],
                            "parse_preview": None,
                        }

                message_parts.append(rendered)
                return StepResult(
                    bot_message="\n\n".join(message_parts),
                    options=list(step.options),
                    step_id=step.id,
                    step_type=step.type,
                    collected=dict(session.collected),
                    report=report_hint,
                    intent_confirm=intent_confirm_meta,
                    collection=collection_meta,
                )

            # message 타입: 축적하고 다음 스텝으로 자동 진행
            message_parts.append(rendered)
            if not step.next or not definition.get_step(step.next):
                session.completed = True
                return StepResult(
                    bot_message="\n\n".join(message_parts),
                    completed=True,
                    concluded=True,
                    collected=dict(session.collected),
                    step_id=step.id,
                    step_type=step.type,
                    report=report_hint,
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
            concluded=True,
            collected=dict(session.collected),
        )
