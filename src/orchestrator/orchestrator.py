"""MasterOrchestrator: 3-Tier 프로필 라우팅 + 크로스도메인 핸드오프.

chatbot_id가 지정되지 않은 요청에 대해:
1. 테넌트 기반 프로필 필터링
2. 워크플로우 재개 감지
3. 컨텍스트 기반 라우팅 (꼬리질문, 과거 프로필 참조)
4. 3-Tier Profile Router (패턴 -> 키워드 스코어링 -> LLM)
"""

from __future__ import annotations

import time

from src.locale.bundle import get_locale
from src.observability.logging import get_logger
from src.orchestrator.embedding_router import EmbeddingRouter
from src.orchestrator.llm_adapter import OrchestratorLLM
from src.orchestrator.models import OrchestratorResult
from src.orchestrator.profile_router import ProfileRouter
from src.orchestrator.tenant import TenantService

logger = get_logger(__name__)

_RESUME_MAX_LENGTH = 80

# 프로필 히스토리 최대 보관 수
_MAX_PROFILE_HISTORY = 10


class MasterOrchestrator:
    """프로필 간 자동 라우팅 + 크로스도메인 핸드오프 오케스트레이터."""

    def __init__(
        self,
        llm: OrchestratorLLM,
        profile_store,
        session_memory,
        workflow_engine,
        tenant_service: TenantService,
        embedding_router: EmbeddingRouter | None = None,
    ):
        self._llm = llm
        self._profile_store = profile_store
        self._session = session_memory
        self._workflow = workflow_engine
        self._tenant = tenant_service
        self._embedding_router = embedding_router

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
                general_message=get_locale().message("no_profiles_available"),
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

        current = meta.get("current_profile_id")

        # 4. 컨텍스트 기반 라우팅 (꼬리질문, 과거 프로필 참조)
        router = ProfileRouter(profiles)
        continuation = self._resolve_continuation(
            question, current, meta, history, router, profiles,
        )
        if continuation:
            logger.info(
                "orchestrator_continuation",
                session_id=session_id,
                profile_id=continuation.selected_profile_id,
                reason=continuation.reason,
            )
            return continuation

        # 5. 3-Tier Profile Router
        valid_ids = {p["id"] for p in profiles}

        # Tier 1: 임베딩 유사도 (~100ms, 다국어 지원)
        if self._embedding_router:
            emb_result = await self._embedding_router.route(question)
            if emb_result and not emb_result.is_ambiguous and emb_result.profile_id in valid_ids:
                logger.info(
                    "orchestrator_tier1_embedding",
                    session_id=session_id,
                    profile_id=emb_result.profile_id,
                    similarity=round(emb_result.similarity, 4),
                    confidence=round(emb_result.confidence, 4),
                )
                result = OrchestratorResult(
                    selected_profile_id=emb_result.profile_id,
                    reason=f"[Tier 1] 임베딩 유사도: {emb_result.similarity:.3f}",
                )
                if current and result.selected_profile_id != current:
                    await self._handle_switch(session_id, current, meta)
                await self._record_profile_history(session_id, result.selected_profile_id, meta)
                return result

        # Tier 1-B: 패턴 매칭 (임베딩 라우터 미사용 또는 모호한 경우 폴백)
        tier1 = router.tier1_rule_match(question)
        if tier1:
            logger.info(
                "orchestrator_tier1_pattern",
                session_id=session_id,
                profile_id=tier1.profile_id,
                reason=tier1.reason,
            )
            result = self._route_result_from_tier(tier1, profiles)
            if current and result.selected_profile_id != current:
                await self._handle_switch(session_id, current, meta)
            await self._record_profile_history(session_id, result.selected_profile_id, meta)
            return result

        # Tier 2: 키워드 스코어링 (<5ms)
        tier2 = router.tier2_keyword_score(question)
        if tier2:
            logger.info(
                "orchestrator_tier2",
                session_id=session_id,
                profile_id=tier2.profile_id,
                reason=tier2.reason,
                confidence=tier2.confidence,
            )
            result = self._route_result_from_tier(tier2, profiles)
            if current and result.selected_profile_id != current:
                await self._handle_switch(session_id, current, meta)
            await self._record_profile_history(session_id, result.selected_profile_id, meta)
            return result

        # Tier 3: LLM Function Calling (최후 수단, 3~10초)
        logger.info("orchestrator_tier3", session_id=session_id, question_len=len(question))
        result = await self._llm_select(question, profiles, history, current)

        # 6. 프로필 전환 시 워크플로우 일시정지
        if current and result.selected_profile_id and current != result.selected_profile_id:
            await self._handle_switch(session_id, current, meta)

        if result.selected_profile_id:
            await self._record_profile_history(session_id, result.selected_profile_id, meta)

        return result

    # ── 컨텍스트 기반 라우팅 ──

    def _resolve_continuation(
        self,
        question: str,
        current_profile: str | None,
        meta: dict,
        history: list[dict],
        router: ProfileRouter,
        profiles: list[dict],
    ) -> OrchestratorResult | None:
        """꼬리질문/컨텍스트 기반으로 프로필을 결정한다."""
        q = question.strip()

        # A. 명시적 과거 프로필 참조: "아까 {keyword}"
        past_ref = get_locale().compiled_pattern("past_reference")
        if past_ref and past_ref.search(q):
            profile_history = meta.get("profile_history", [])
            for entry in reversed(profile_history):
                pid = entry["profile_id"]
                profile_keywords = router.get_keywords(pid)
                if any(kw in q for kw, _ in profile_keywords):
                    return OrchestratorResult(
                        selected_profile_id=pid,
                        reason=f"과거 프로필 참조: {pid}",
                    )

        if not current_profile:
            return None

        # 현재 프로필이 사용 가능한 프로필에 있는지 확인
        profile_ids = [p["id"] for p in profiles]
        if current_profile not in profile_ids:
            return None

        # B. 대명사/연속 표현 -> 현재 프로필 유지
        #    단, 다른 프로필 키워드가 감지되면 프로필 전환 허용
        continuation = get_locale().compiled_pattern("continuation")
        if continuation and continuation.match(q):
            tier1_check = router.tier1_rule_match(q)
            if tier1_check and tier1_check.profile_id != current_profile:
                # 다른 프로필 키워드 감지 -> continuation 적용 안 함
                return None
            return OrchestratorResult(
                selected_profile_id=current_profile,
                reason="대명사/연속 표현",
                is_continuation=True,
            )

        # C. 짧은 질문 (<15자) + 현재 프로필 존재 + 대화 이력 있음 -> 유지
        #    단, 다른 프로필 키워드가 매칭되면 continuation 적용 안 함
        if len(q) <= 15 and history:
            tier1_check = router.tier1_rule_match(q)
            if tier1_check is None or tier1_check.profile_id == current_profile:
                return OrchestratorResult(
                    selected_profile_id=current_profile,
                    reason="짧은 후속 질문",
                    is_continuation=True,
                )

        return None

    # ── 프로필 히스토리 ──

    async def _record_profile_history(
        self, session_id: str, profile_id: str, meta: dict,
    ) -> None:
        """프로필 전환 히스토리를 기록하고 current_profile_id를 갱신한다."""
        # current_profile_id 갱신
        meta["current_profile_id"] = profile_id

        history = meta.setdefault("profile_history", [])
        # 같은 프로필 연속 기록 방지
        if not (history and history[-1]["profile_id"] == profile_id):
            history.append({
                "profile_id": profile_id,
                "switched_at": time.time(),
            })
            # 최대 개수 유지
            meta["profile_history"] = history[-_MAX_PROFILE_HISTORY:]

        await self._session.save_orchestrator_metadata(session_id, meta)

    # ── 내부 헬퍼 ──

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
        """워크플로우 재개 의도인지 패턴 매칭으로 판단한다."""
        stripped = question.strip()
        if len(stripped) > _RESUME_MAX_LENGTH:
            return False
        pattern = get_locale().compiled_pattern("resume")
        return bool(pattern and pattern.search(stripped))

    def _route_result_from_tier(
        self, tier_result, profiles: list[dict],
    ) -> OrchestratorResult:
        """RouteResult를 OrchestratorResult로 변환한다."""
        # 인사 패턴이면 general-chat으로 라우팅 (is_general_response 아님, 프로필 전달)
        profile_id = tier_result.profile_id
        valid_ids = {p["id"] for p in profiles}
        if profile_id not in valid_ids:
            profile_id = profiles[0]["id"]
        return OrchestratorResult(
            selected_profile_id=profile_id,
            reason=f"[Tier {tier_result.tier}] {tier_result.reason}",
        )

    async def _llm_select(
        self,
        question: str,
        profiles: list[dict],
        history: list[dict],
        current_profile: str | None,
    ) -> OrchestratorResult:
        """LLM Function Calling으로 프로필을 선택한다 (Tier 3)."""
        try:
            result = await self._llm.select_profile(question, profiles, history)
        except Exception as e:
            logger.error("orchestrator_llm_error", error=str(e), exc_info=True)
            # 폴백: 현재 프로필 -> 첫 번째 프로필
            fallback = current_profile or (profiles[0]["id"] if profiles else "")
            if not fallback:
                return OrchestratorResult(
                    selected_profile_id="",
                    reason="LLM 오류 + 프로필 없음",
                    is_general_response=True,
                    general_message=get_locale().message("service_error"),
                )
            return OrchestratorResult(
                selected_profile_id=fallback,
                reason=f"LLM 오류 폴백: {e}",
            )

        fn = result.get("function")
        valid_ids = {p["id"] for p in profiles}

        if fn == "select_profile":
            profile_id = result.get("profile_id", "")
            if profile_id not in valid_ids:
                logger.warning(
                    "orchestrator_invalid_profile",
                    selected=profile_id,
                    available=list(valid_ids),
                )
                fallback = current_profile or profiles[0]["id"]
                return OrchestratorResult(
                    selected_profile_id=fallback,
                    reason=f"[Tier 3] LLM이 잘못된 프로필 선택 ({profile_id}), 폴백",
                )

            logger.info(
                "orchestrator_profile_selected",
                profile_id=profile_id,
                reason=result.get("reason", ""),
            )
            return OrchestratorResult(
                selected_profile_id=profile_id,
                reason=f"[Tier 3] {result.get('reason', '')}",
            )

        if fn == "no_tool_call":
            # Tier 2 재시도 (min_score=0.3으로 완화)
            router = ProfileRouter(profiles)
            tier2_retry = router.tier2_keyword_score(question, min_score=0.3)
            if tier2_retry:
                logger.info(
                    "orchestrator_tier3_tier2_retry",
                    profile_id=tier2_retry.profile_id,
                    confidence=tier2_retry.confidence,
                )
                return OrchestratorResult(
                    selected_profile_id=tier2_retry.profile_id,
                    reason=f"[Tier 3] Tier 2 재시도: {tier2_retry.reason}",
                )
            # general-chat 우선 폴백
            general = next((p["id"] for p in profiles if p["id"] == "general-chat"), None)
            fallback = current_profile or general or profiles[0]["id"]
            logger.warning(
                "orchestrator_no_tool_call_fallback",
                text_preview=result.get("text", "")[:100],
                fallback=fallback,
            )
            return OrchestratorResult(
                selected_profile_id=fallback,
                reason="[Tier 3] tool_calls 없음, 폴백",
            )

        # 예상치 못한 응답
        return OrchestratorResult(
            selected_profile_id=current_profile or (profiles[0]["id"] if profiles else ""),
            reason="[Tier 3] 예상치 못한 LLM 응답, 폴백",
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
                "paused_at": time.time(),
            }
            # 메타에 paused_workflow 저장 (타임스탬프로 충돌 감지)
            existing_paused = meta.get("paused_workflow")
            if existing_paused and existing_paused.get("paused_at", 0) > time.time() - 1.0:
                # 1초 이내 다른 요청이 이미 pause → 스킵
                logger.warning(
                    "orchestrator_pause_conflict",
                    session_id=session_id,
                    existing_wf=existing_paused.get("workflow_id"),
                )
                return

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
