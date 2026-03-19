"""MasterOrchestrator: 프로필 자동 라우팅 + 크로스도메인 핸드오프.

chatbot_id가 지정되지 않은 요청에 대해:
1. 테넌트 기반 프로필 필터링
2. 연속 대화 휴리스틱 (LLM 호출 회피)
3. 워크플로우 재개 감지
4. LLM Function Calling으로 프로필 선택
"""

from __future__ import annotations

import re

from src.observability.logging import get_logger
from src.orchestrator.llm_adapter import OrchestratorLLM
from src.orchestrator.models import OrchestratorResult
from src.orchestrator.tenant import TenantService

logger = get_logger(__name__)

# 워크플로우 재개 의도 패턴 — 짧은 문장에서만 매칭 (30자 이내)
_RESUME_PATTERNS = re.compile(
    r"다시|이어서|계속|재개|resume|continue",
    re.IGNORECASE,
)
_RESUME_MAX_LENGTH = 30

# 대명사 / 연속 대화 힌트 (문장 시작 패턴)
_CONTINUATION_PATTERNS = re.compile(
    r"^(이거|그거|저거|이것|그것|저것|그래|네|응|맞아|ㅇㅇ|그리고|또|추가로)",
)


class MasterOrchestrator:
    """프로필 간 자동 라우팅 + 크로스도메인 핸드오프 오케스트레이터."""

    def __init__(
        self,
        llm: OrchestratorLLM,
        profile_store,
        session_memory,
        workflow_engine,
        tenant_service: TenantService,
    ):
        self._llm = llm
        self._profile_store = profile_store
        self._session = session_memory
        self._workflow = workflow_engine
        self._tenant = tenant_service

    async def route(
        self,
        question: str,
        session_id: str,
        user_ctx,
    ) -> OrchestratorResult:
        """질문을 분석하여 적절한 프로필로 라우팅한다."""
        # 1. 테넌트 기반 프로필 필터링
        profiles = await self._get_available_profiles(user_ctx)
        if not profiles:
            return OrchestratorResult(
                selected_profile_id="",
                reason="사용 가능한 프로필 없음",
                is_general_response=True,
                general_message="현재 사용 가능한 서비스가 없습니다. 관리자에게 문의하세요.",
            )

        # 2. 세션 메타데이터 로드
        meta = await self._session.get_orchestrator_metadata(session_id)
        history = await self._session.get_turns(session_id, max_turns=5)

        # 3. 워크플로우 재개 감지
        if self._is_resume_intent(question) and meta.get("paused_workflow"):
            paused = meta["paused_workflow"]
            logger.info(
                "orchestrator_resume_workflow",
                session_id=session_id,
                profile_id=paused["profile_id"],
                workflow_id=paused.get("workflow_id"),
            )
            return OrchestratorResult(
                selected_profile_id=paused["profile_id"],
                reason="워크플로우 재개",
                should_resume_workflow=True,
                paused_state=paused,
            )

        # 4. 연속 대화 휴리스틱 (LLM 호출 회피)
        current = meta.get("current_profile_id")
        if current and self._likely_continuation(question, history):
            # 현재 프로필이 사용 가능한 프로필에 있는지 확인
            profile_ids = [p["id"] for p in profiles]
            if current in profile_ids:
                logger.info(
                    "orchestrator_continuation",
                    session_id=session_id,
                    profile_id=current,
                    question_len=len(question),
                )
                return OrchestratorResult(
                    selected_profile_id=current,
                    reason="대화 연속",
                    is_continuation=True,
                )

        # 5. LLM Function Calling으로 프로필 선택
        result = await self._llm_select(question, profiles, history)

        # 6. 프로필 전환 시 워크플로우 일시정지
        if current and result.selected_profile_id and current != result.selected_profile_id:
            await self._handle_switch(session_id, current, meta)

        return result

    async def _get_available_profiles(self, user_ctx) -> list[dict]:
        """테넌트 + API Key 권한 기반으로 사용 가능한 프로필 목록을 반환한다."""
        all_profiles = await self._profile_store.list_all()

        # API Key의 allowed_profiles 필터
        api_allowed = set(user_ctx.allowed_profiles) if user_ctx.allowed_profiles else None

        # 테넌트 필터
        tenant_id = getattr(user_ctx, "tenant_id", None)
        tenant_allowed = None
        if tenant_id:
            tenant_profile_ids = await self._tenant.get_allowed_profiles(tenant_id)
            if tenant_profile_ids:
                tenant_allowed = set(tenant_profile_ids)

        result = []
        for p in all_profiles:
            if api_allowed and p.id not in api_allowed:
                continue
            if tenant_allowed and p.id not in tenant_allowed:
                continue
            result.append({
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "domain_scopes": p.domain_scopes,
                "intent_hints": [
                    {"name": h.name, "patterns": h.patterns, "description": h.description}
                    for h in p.intent_hints
                ],
            })

        return result

    def _is_resume_intent(self, question: str) -> bool:
        """워크플로우 재개 의도인지 패턴 매칭으로 판단한다.

        "다시 설명해줘" 같은 긴 문장에서의 false positive를 방지하기 위해
        짧은 질문(30자 이내)에서만 매칭한다.
        """
        stripped = question.strip()
        if len(stripped) > _RESUME_MAX_LENGTH:
            return False
        return bool(_RESUME_PATTERNS.search(stripped))

    def _likely_continuation(self, question: str, history: list[dict]) -> bool:
        """연속 대화인지 휴리스틱으로 판단한다.

        대명사/연속 표현이 문장 시작에 있을 때만 연속으로 판단한다.
        짧은 질문 단독으로는 판단하지 않는다 — "수수료 얼마?" 같은
        크로스도메인 질문이 차단되는 것을 방지.
        """
        if not history:
            return False

        stripped = question.strip()

        # 대명사/연속 표현으로 시작하는 경우만 연속 대화로 판단
        if _CONTINUATION_PATTERNS.match(stripped):
            return True

        return False

    async def _llm_select(
        self,
        question: str,
        profiles: list[dict],
        history: list[dict],
    ) -> OrchestratorResult:
        """LLM Function Calling으로 프로필을 선택한다."""
        try:
            result = await self._llm.select_profile(question, profiles, history)
        except Exception as e:
            logger.error("orchestrator_llm_error", error=str(e), exc_info=True)
            # 폴백: 첫 번째 프로필 선택
            if profiles:
                return OrchestratorResult(
                    selected_profile_id=profiles[0]["id"],
                    reason=f"LLM 오류 폴백: {e}",
                )
            return OrchestratorResult(
                selected_profile_id="",
                reason="LLM 오류 + 프로필 없음",
                is_general_response=True,
                general_message="서비스에 일시적인 문제가 발생했습니다.",
            )

        fn = result.get("function")

        if fn == "general_response":
            return OrchestratorResult(
                selected_profile_id="",
                reason="일반 응답 (인사/잡담)",
                is_general_response=True,
                general_message=result.get("message", ""),
            )

        if fn == "select_profile":
            profile_id = result.get("profile_id", "")
            # 선택된 프로필이 목록에 있는지 검증
            valid_ids = {p["id"] for p in profiles}
            if profile_id not in valid_ids:
                logger.warning(
                    "orchestrator_invalid_profile",
                    selected=profile_id,
                    available=list(valid_ids),
                )
                # 폴백: 첫 번째 프로필
                return OrchestratorResult(
                    selected_profile_id=profiles[0]["id"],
                    reason=f"LLM이 잘못된 프로필 선택 ({profile_id}), 폴백",
                )

            logger.info(
                "orchestrator_profile_selected",
                profile_id=profile_id,
                reason=result.get("reason", ""),
            )
            return OrchestratorResult(
                selected_profile_id=profile_id,
                reason=result.get("reason", ""),
            )

        # 예상치 못한 응답
        return OrchestratorResult(
            selected_profile_id=profiles[0]["id"] if profiles else "",
            reason="예상치 못한 LLM 응답, 폴백",
        )

    async def _handle_switch(
        self,
        session_id: str,
        current_profile_id: str,
        meta: dict,
    ) -> None:
        """프로필 전환 시 활성 워크플로우를 일시정지한다."""
        active_wf = self._workflow.get_session(session_id)
        if active_wf and not active_wf.completed:
            paused_state = {
                "workflow_id": active_wf.workflow_id,
                "step_id": active_wf.current_step_id,
                "collected": dict(active_wf.collected),
                "profile_id": current_profile_id,
            }
            # 메타에 paused_workflow 저장
            meta["paused_workflow"] = paused_state
            await self._session.save_orchestrator_metadata(session_id, meta)

            # 워크플로우 메모리에서 제거
            self._workflow.cancel(session_id)

            logger.info(
                "orchestrator_workflow_paused",
                session_id=session_id,
                workflow_id=active_wf.workflow_id,
                step_id=active_wf.current_step_id,
                collected_keys=list(active_wf.collected.keys()),
            )
