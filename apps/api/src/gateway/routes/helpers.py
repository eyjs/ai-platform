"""Gateway 라우트 공용 헬퍼.

router.py god-file 분할(Step22 G25). 순수 이동: 인증/레이트리밋/세션 스코프 해석/
메모리 추출/graceful shutdown 카운터/채팅 세팅(_prepare_chat*) 등 라우트 간 공용
로직을 한곳에 모은다. routes/* 모듈은 서로 import하지 않고 이 모듈만 의존한다(순환 방지).
"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from src.domain.agent_context import AgentContext
from src.domain.models import AgentMode, SearchScope
from src.gateway.auth import AuthError
from src.gateway.models import ChatRequest, UserContext
from src.gateway.concurrency_gate import RETRY_AFTER_SECONDS
from src.gateway.rate_limiter import build_client_id
from src.infrastructure.db.tenant_context import current_tenant
from src.infrastructure.memory.memory_extractor import MemoryExtractor
from src.observability.logging import RequestContext, get_logger, request_context
from src.observability.trace_logger import RequestTrace
from src.domain.execution_plan import ExecutionPlan
from src.workflow.engine import StepResult

APP_VERSION = "0.1.0"

ROLE_LEVELS = {"VIEWER": 0, "EDITOR": 1, "REVIEWER": 2, "APPROVER": 3, "ADMIN": 4}

logger = get_logger(__name__)

# Graceful shutdown 지원
_active_requests: int = 0
_shutdown_event = asyncio.Event()

_memory_extractor = MemoryExtractor()


async def _save_extracted_memories(
    state: object,
    tenant_id: str,
    turns: list[dict],
    retention_days: int | None,
) -> None:
    """대화에서 사실을 추출하여 MemoryStore에 저장한다 (fire-and-forget용)."""
    try:
        memory_store = getattr(state, "memory_store", None)
        if memory_store is None:
            return

        facts = _memory_extractor.extract_facts(turns)
        for fact in facts:
            await memory_store.save_memory(
                tenant_id=tenant_id,
                key=fact["key"],
                value=fact["value"],
                memory_type=fact.get("memory_type", "fact"),
                retention_days=retention_days,
            )

        if facts:
            logger.info(
                "memory_facts_saved",
                tenant_id=tenant_id,
                facts_count=len(facts),
            )
    except Exception as e:
        logger.warning("memory_extraction_failed", error=str(e))


def increment_active() -> None:
    """활성 요청 수 증가."""
    global _active_requests
    _active_requests += 1


def decrement_active() -> None:
    """활성 요청 수 감소."""
    global _active_requests
    _active_requests = max(0, _active_requests - 1)


async def wait_for_pending_requests(timeout: float = 30.0) -> None:
    """진행 중인 요청이 완료될 때까지 대기 (최대 timeout초)."""
    if _active_requests == 0:
        return

    logger.info("waiting_for_pending_requests", active_count=_active_requests, timeout=timeout)
    start_time = time.monotonic()

    while _active_requests > 0:
        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            logger.warning("graceful_shutdown_timeout", active_requests=_active_requests, elapsed=elapsed)
            break
        await asyncio.sleep(0.1)

    logger.info("pending_requests_complete", active_requests=_active_requests)


def _get_app_state(request: Request):
    """FastAPI app.state에서 컴포넌트를 가져온다."""
    return request.app.state


async def _resolve_session_scope_id(state, session_id: str) -> str | None:
    """세션 업로드 문서 스코프 격리(Step26) 키를 해석한다.

    해당 세션에 업로드된 문서(metadata.session_id로 태깅, /documents/session-upload
    경유)가 있을 때만 session_id를 반환한다. 그러면 이후 검색은 RAG SQL의
    `documents.metadata->>'session_id'` additive 필터로 해당 세션 문서에만 격리된다.
    업로드가 없으면 None을 반환하여 일반 검색(전체 도메인) 동작을 유지한다.
    조회 실패는 격리 부재로 폴백하지 않고 None(일반 검색)으로 안전하게 처리한다.
    """
    try:
        meta = await state.session_memory.get_orchestrator_metadata(session_id) or {}
    except Exception as e:
        logger.warning("session_scope_resolve_failed", session_id=session_id, error=str(e))
        return None
    if meta.get("uploaded_external_ids"):
        return session_id
    return None


async def _authenticate(request: Request) -> UserContext:
    """요청을 인증하고 UserContext를 반환한다."""
    state = _get_app_state(request)
    auth_service = state.auth_service

    try:
        user_ctx = await auth_service.authenticate(
            authorization=request.headers.get("Authorization"),
            api_key=request.headers.get("X-API-Key"),
        )
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    # Origin 도메인 화이트리스트 검증
    try:
        auth_service.check_origin(
            user_ctx,
            origin=request.headers.get("Origin"),
        )
    except AuthError as e:
        raise HTTPException(status_code=403, detail=str(e))

    # RLS(4c): 이 요청의 DB 접근이 테넌트로 강제되도록 컨텍스트변수 설정.
    # 풀 setup 훅이 매 acquire마다 이 값으로 SET ROLE + GUC를 건다.
    settings = getattr(state, "settings", None)
    default_tenant = getattr(settings, "default_tenant_id", "default") if settings else "default"
    current_tenant.set(user_ctx.tenant_id or default_tenant)

    return user_ctx


async def _check_rate_limit(
    request: Request,
    user_ctx: UserContext,
    sub_key: str | None = None,
) -> None:
    """Token Bucket Rate Limiting. UserContext.rate_limit_per_min 기반.

    축(B5): API 키별 분리 + 같은 공유키 내 세션별 분리. sub_key로 session_id를
    넘기면 한 세션의 폭주가 같은 키의 다른 세션을 굶기지 않는다.
    """
    state = _get_app_state(request)
    fallback = request.client.host if request.client else "anonymous"
    client_id = build_client_id(user_ctx, sub_key=sub_key, fallback=fallback)
    await state.rate_limiter.verify_request(
        client_id=client_id,
        rate_limit_per_min=user_ctx.rate_limit_per_min,
    )


def _acquire_agent_slot(request: Request) -> None:
    """전역 동시 실행 슬롯을 잡는다. 상한 초과면 503 + Retry-After로 즉시 거부한다.

    레이트리밋(사용자별 속도)과 다른 축이다 — 각자 한도 내에 있어도 전부 합치면
    프로세스가 감당 못 하는 수가 될 수 있고, 그때 PG 풀·LLM 큐가 먼저 무너진다.

    **해제 책임은 호출자에게 있다.** 잡았으면 반드시 _release_agent_slot을 부른다
    (비스트리밍은 finally, 스트리밍은 제너레이터 finally — 슬롯은 스트림이 끝날 때까지
    점유돼야 하므로 핸들러 반환 시점에 놓으면 상한이 무의미해진다).
    """
    gate = _get_app_state(request).concurrency_gate
    if gate.try_acquire():
        return
    raise HTTPException(
        status_code=HTTP_503_SERVICE_UNAVAILABLE,
        detail="서버가 처리 가능한 동시 요청 수를 초과했습니다. 잠시 후 다시 시도해 주세요.",
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


def _release_agent_slot(request: Request) -> None:
    """_acquire_agent_slot으로 잡은 슬롯을 놓는다."""
    _get_app_state(request).concurrency_gate.release()


def _is_supervisor_request(chatbot_id: str | None, state) -> bool:
    """chat/chat_stream 엔트리 supervisor 분기 판별 (task-002, §0-2 / Phase 3 컷오버).

    순수 판별 함수 — 부작용 없음. `state.supervisor`가 배선되지 않은(None) 워크트리/환경에서는
    항상 False를 반환해 직접 모드로 안전 폴백한다(chatbot_id 미지정이면 400).

    Phase 3 컷오버: 자동 라우팅(chatbot_id 미지정)은 전부 supervisor가 처리한다 —
    라우팅 = 1위임의 특수케이스. 레거시 MasterOrchestrator 경로는 제거됨.
    직접 모드(특정 chatbot_id)는 절대 여기 걸리지 않는다(§0-1 무변경).
    """
    if getattr(state, "supervisor", None) is None:
        return False
    sid = getattr(getattr(state, "settings", None), "supervisor_profile_id", "supervisor")
    return chatbot_id == sid or chatbot_id is None


@dataclass
class _ChatSetup:
    """chat/chat_stream 공통 세팅 결과."""

    session_id: str
    plan: object  # ExecutionPlan
    context: AgentContext
    trace: RequestTrace
    ctx_token: object  # contextvars.Token
    profile_id: str = ""
    # 직접 모드는 항상 False — 자동 라우팅(orchestrated=True)은 supervisor 엔트리가 처리.
    orchestrated: bool = False


async def _prepare_chat(
    req: ChatRequest,
    request: Request,
    user_ctx: UserContext,
) -> _ChatSetup:
    """chat/chat_stream 공통 로직 (직접 모드 전용): 인증 -> Profile 로딩 -> 세션 -> history -> Router.

    자동 라우팅(chatbot_id 미지정)은 엔트리에서 supervisor로 분기되므로(Phase 3 컷오버)
    여기 도달하는 요청은 항상 특정 chatbot_id를 가진다. 미지정으로 도달한 경우는
    supervisor 미배선 환경뿐 — 400으로 명시 거절한다.
    """
    state = _get_app_state(request)
    request_id = str(uuid.uuid4())
    session_id = req.session_id or str(uuid.uuid4())

    chatbot_id = req.chatbot_id
    if not chatbot_id:
        raise HTTPException(
            status_code=400,
            detail="chatbot_id가 필요합니다. (자동 라우팅은 supervisor가 담당하며 현재 비활성 상태입니다)",
        )

    ctx_token = request_context.set(RequestContext(
        request_id=request_id,
        session_id=session_id,
        profile_id=chatbot_id,
        user_id=user_ctx.user_id,
    ))

    try:
        # 프로필 접근 권한 확인
        try:
            await state.auth_service.check_profile_access(user_ctx, chatbot_id)
        except AuthError as e:
            raise HTTPException(status_code=403, detail=str(e))

        logger.info(
            "chat_request",
            question=req.question[:100],
            chatbot_id=chatbot_id,
            question_len=len(req.question),
            user_id=user_ctx.user_id,
            user_role=user_ctx.user_role,
        )

        profile = await state.profile_store.get(chatbot_id)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile not found: {chatbot_id}")

        # directive가 있으면 backend 오케스트레이터의 generate_turn 호출 →
        # ai-platform 워크플로우를 우회하고 강제 agentic grounded 대화로 처리한다.
        # (플로우 회수: 비즈니스 플로우는 saju 백엔드가 소유, ai-platform은 추론만.)
        force_agentic = bool(req.directive and req.directive.strip())

        # 활성 워크플로우 세션이 있으면 Router + history 로드 바이패스 (force_agentic이면 무시)
        active_wf = await state.workflow_engine.get_session(session_id)
        if active_wf and not active_wf.completed and not force_agentic:
            logger.info(
                "workflow_session_active",
                session_id=session_id,
                workflow_id=active_wf.workflow_id,
                current_step=active_wf.current_step_id,
            )
            plan = ExecutionPlan(
                mode=AgentMode.WORKFLOW,
                scope=SearchScope(),
                workflow_id=active_wf.workflow_id,
            )
            history = []
        else:
            await state.session_memory.create_session(
                session_id=session_id,
                profile_id=profile.id,
                user_id=user_ctx.user_id,
                ttl_seconds=profile.memory_ttl_seconds,
                tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
            )
            # 호출자(백엔드)가 history를 주면 그것을 신뢰원천으로 사용(멀티턴 고아/유실 방지).
            # 없을 때만 session_memory 폴백.
            if req.history:
                history = [
                    {"role": h.get("role"), "content": h.get("content")}
                    for h in req.history
                    if h.get("role") and h.get("content")
                ]
            else:
                history = await state.session_memory.get_turns(session_id, max_turns=profile.memory_max_turns)

            skip_context_resolve = req.chatbot_id is not None

            session_scope_id = await _resolve_session_scope_id(state, session_id)
            tools = state.tool_registry.resolve(profile.tool_names)
            plan = await state.ai_router.route(
                query=req.question,
                profile=profile,
                tools=tools,
                history=history,
                user_security_level=user_ctx.security_level_max,
                skip_context_resolve=skip_context_resolve,
                external_context=req.context or "",
                tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
                session_scope_id=session_scope_id,
            )

        agent_context = AgentContext(
            session_id=session_id,
            user_id=user_ctx.user_id,
            user_role=user_ctx.user_role,
            conversation_history=history,
            metadata=req.metadata or {},
            tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
        )
        trace = RequestTrace(request_id=request_id)

        # force_agentic: mode_selector가 워크플로우를 골랐어도 강제 agentic으로 (플로우 회수)
        if force_agentic and plan.mode != AgentMode.AGENTIC:
            plan.mode = AgentMode.AGENTIC
            logger.info("force_agentic_for_directive", session_id=session_id)

        # directive 주입: per-turn 가변 지시문 → volatile system_prompt 위치 (캐시 불가 영역)
        _inject_directive(plan, req.directive)

        return _ChatSetup(
            session_id=session_id,
            plan=plan,
            context=agent_context,
            trace=trace,
            ctx_token=ctx_token,
            profile_id=chatbot_id,
        )
    except Exception:
        request_context.reset(ctx_token)
        raise


async def _prepare_chat_fast(
    req: ChatRequest,
    request: Request,
    user_ctx: UserContext,
) -> _ChatSetup:
    """chat_stream 전용 (직접 모드): 무거운 전처리 없이 즉시 반환.

    자동 라우팅(chatbot_id 미지정)은 엔트리에서 supervisor로 분기되므로(Phase 3 컷오버)
    여기 도달하는 요청은 항상 특정 chatbot_id를 가진다.
    """
    state = _get_app_state(request)
    request_id = str(uuid.uuid4())
    session_id = req.session_id or str(uuid.uuid4())

    chatbot_id = req.chatbot_id
    if not chatbot_id:
        raise HTTPException(
            status_code=400,
            detail="chatbot_id가 필요합니다. (자동 라우팅은 supervisor가 담당하며 현재 비활성 상태입니다)",
        )

    ctx_token = request_context.set(RequestContext(
        request_id=request_id,
        session_id=session_id,
        profile_id=chatbot_id,
        user_id=user_ctx.user_id,
    ))

    try:
        # 프로필 접근 권한 확인
        try:
            await state.auth_service.check_profile_access(user_ctx, chatbot_id)
        except AuthError as e:
            raise HTTPException(status_code=403, detail=str(e))

        logger.info(
            "chat_stream_fast_setup",
            question=req.question[:100],
            chatbot_id=chatbot_id,
            question_len=len(req.question),
            user_id=user_ctx.user_id,
        )

        profile = await state.profile_store.get(chatbot_id)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile not found: {chatbot_id}")

        # 세션 생성 (없으면)
        await state.session_memory.create_session(
            session_id=session_id,
            profile_id=chatbot_id,
            user_id=user_ctx.user_id,
            ttl_seconds=profile.memory_ttl_seconds,
            tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
        )

        # directive가 있으면 backend 오케스트레이터의 generate_turn 호출 →
        # ai-platform 워크플로우를 우회하고 강제 agentic grounded 대화로 처리한다.
        # (플로우 회수: 비즈니스 플로우는 saju 백엔드가 소유, ai-platform은 추론만.)
        force_agentic = bool(req.directive and req.directive.strip())

        # 활성 워크플로우 세션이 있으면 Router + history 로드 바이패스 (force_agentic이면 무시)
        active_wf = await state.workflow_engine.get_session(session_id)
        if active_wf and not active_wf.completed and not force_agentic:
            logger.info(
                "workflow_session_active",
                session_id=session_id,
                workflow_id=active_wf.workflow_id,
                current_step=active_wf.current_step_id,
            )
            plan = ExecutionPlan(
                mode=AgentMode.WORKFLOW,
                scope=SearchScope(),
                workflow_id=active_wf.workflow_id,
            )
            history = []
        else:
            # 호출자(백엔드)가 history를 주면 그것을 신뢰원천으로 사용(멀티턴 고아/유실 방지).
            # 없을 때만 session_memory 폴백.
            if req.history:
                history = [
                    {"role": h.get("role"), "content": h.get("content")}
                    for h in req.history
                    if h.get("role") and h.get("content")
                ]
            else:
                history = await state.session_memory.get_turns(session_id, max_turns=profile.memory_max_turns)

            # chatbot_id가 명시적으로 전달된 경우: L0 ContextResolver를 건너뛴다
            # 이미 특정 챗봇을 지정했으므로 대명사 해소/질문 재작성이 불필요
            skip_context_resolve = req.chatbot_id is not None

            session_scope_id = await _resolve_session_scope_id(state, session_id)
            tools = state.tool_registry.resolve(profile.tool_names)
            plan = await state.ai_router.route(
                query=req.question,
                profile=profile,
                tools=tools,
                history=history,
                user_security_level=user_ctx.security_level_max,
                skip_context_resolve=skip_context_resolve,
                external_context=req.context or "",
                tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
                session_scope_id=session_scope_id,
            )

        agent_context = AgentContext(
            session_id=session_id,
            user_id=user_ctx.user_id,
            user_role=user_ctx.user_role,
            conversation_history=history,
            metadata=req.metadata or {},
            tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
        )
        trace = RequestTrace(request_id=request_id)

        # force_agentic: mode_selector가 워크플로우를 골랐어도 강제 agentic으로 (플로우 회수)
        if force_agentic and plan.mode != AgentMode.AGENTIC:
            plan.mode = AgentMode.AGENTIC
            logger.info("force_agentic_for_directive", session_id=session_id)

        # directive 주입: per-turn 가변 지시문 → volatile system_prompt 위치 (캐시 불가 영역)
        _inject_directive(plan, req.directive)

        return _ChatSetup(
            session_id=session_id,
            plan=plan,
            context=agent_context,
            trace=trace,
            ctx_token=ctx_token,
            profile_id=chatbot_id,
        )
    except Exception:
        request_context.reset(ctx_token)
        raise


def _inject_directive(plan: ExecutionPlan, directive: str | None) -> None:
    """directive를 ExecutionPlan.system_prompt의 volatile(per-turn) 위치에 append한다.

    directive는 이 턴에만 유효한 묘묘 행동 지시문 (예: "의도 되묻기 단계").
    strategy_builder가 빌드한 system_prompt(cacheable 부분) 뒤에 붙여 LLM 행동을 유도한다.
    directive 미전달 시 plan 무변경 → 하위호환 보장.

    캐싱 정합:
      - context(grounding)는 strategy_builder external_context 경로 → cacheable 영역.
      - directive는 이 함수에서 plan.system_prompt 끝에 append → volatile(per-turn) 영역.
      실제 캐시 키 분리는 task-101(strategy_builder/engine)의 책임이며,
      이 함수는 directive가 volatile 위치에 흐르도록 라우팅만 책임진다.
    """
    if not directive or not directive.strip():
        return
    separator = "\n\n--- 이 턴 지시 ---\n"
    # volatile(per-turn, 캐시 밖)에 append — cacheable(페르소나+grounding)을 byte-stable로 유지.
    # (이전엔 plan.system_prompt(cacheable)에 붙여 매턴 캐시 prefix가 깨지고 페르소나가 희석됐음.)
    plan.volatile_system_prompt = (
        plan.volatile_system_prompt + separator + directive.strip()
        if plan.volatile_system_prompt
        else directive.strip()
    )


def _step_to_response(result: StepResult) -> dict:
    """StepResult를 JSON 응답으로 변환한다."""
    return {
        "message": result.bot_message,
        "options": result.options,
        "step_id": result.step_id,
        "step_type": result.step_type,
        "collected": result.collected,
        "completed": result.completed,
    }
