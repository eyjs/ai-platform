"""AI Gateway: FastAPI 엔드포인트.

/chat/stream, /chat, /documents/ingest, /profiles, /health

인증:
- /health, /profiles -> 공개 (인증 불필요)
- /chat, /chat/stream, /documents/ingest -> 인증 필수 (JWT 또는 API Key)
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from sse_starlette.sse import EventSourceResponse

from src.domain.models import AgentMode, AgentResponse, SearchScope, UserRole
from src.gateway.auth import AuthError
from src.gateway.rate_limiter import build_client_id
from src.infrastructure.db.tenant_context import current_tenant
from src.gateway.gateway_hooks import (
    latency_timer, safe_enqueue, should_use_cache, try_cache_get, try_cache_put,
)
from src.gateway.models import (
    ChatRequest, IngestJobStatus, IngestRequest, IngestResponse,
    SessionHistoryResponse, SessionListResponse, SessionListItem, UserContext,
    WorkflowAdvanceRequest, WorkflowStartRequest,
)
from src.observability.logging import RequestContext, get_logger, request_context
from src.observability.request_log_models import RequestLogEntry
from src.orchestrator.models import OrchestratorResult
from src.router.execution_plan import ExecutionPlan
from src.services.feedback_models import (
    AdminFeedbackPage,
    FeedbackRequest,
    FeedbackResponse,
)
from src.workflow.engine import StepResult
from src.observability.trace_logger import RequestTrace
from src.domain.agent_context import AgentContext
from src.infrastructure.memory.memory_extractor import MemoryExtractor

APP_VERSION = "0.1.0"

ROLE_LEVELS = {"VIEWER": 0, "EDITOR": 1, "REVIEWER": 2, "APPROVER": 3, "ADMIN": 4}

logger = get_logger(__name__)

# Graceful shutdown 지원
_active_requests: int = 0
_shutdown_event = asyncio.Event()

gateway_router = APIRouter()

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


@dataclass
class _ChatSetup:
    """chat/chat_stream 공통 세팅 결과."""

    session_id: str
    plan: object  # ExecutionPlan
    context: AgentContext
    trace: RequestTrace
    ctx_token: object  # contextvars.Token
    profile_id: str = ""
    orchestrated: bool = False
    needs_routing: bool = False  # 백그라운드 오케스트레이터 라우팅 필요 여부


async def _prepare_chat(
    req: ChatRequest,
    request: Request,
    user_ctx: UserContext,
) -> _ChatSetup:
    """chat/chat_stream 공통 로직: 인증 -> Orchestrator/Profile 로딩 -> 세션 -> history -> Router."""
    state = _get_app_state(request)
    request_id = str(uuid.uuid4())
    session_id = req.session_id or str(uuid.uuid4())

    # Orchestrator 분기: chatbot_id가 없으면 자동 라우팅
    chatbot_id = req.chatbot_id
    orchestrator_result: Optional[OrchestratorResult] = None

    if chatbot_id is None and hasattr(state, "orchestrator") and state.orchestrator:
        orchestrator_result = await state.orchestrator.route(
            question=req.question,
            session_id=session_id,
            user_ctx=user_ctx,
        )

        if orchestrator_result.is_general_response:
            # 인사/잡담 -> 직접 응답 (프로필 없이)
            ctx_token = request_context.set(RequestContext(
                request_id=request_id,
                session_id=session_id,
                profile_id="orchestrator",
                user_id=user_ctx.user_id,
            ))
            try:
                plan = ExecutionPlan(
                    mode=AgentMode.DETERMINISTIC,
                    scope=SearchScope(),
                    direct_answer=orchestrator_result.general_message,
                )
                context = AgentContext(
                    session_id=session_id,
                    user_id=user_ctx.user_id,
                    user_role=user_ctx.user_role,
                    conversation_history=[],
                    metadata=req.metadata or {},
                )
                trace = RequestTrace(request_id=request_id)
                return _ChatSetup(
                    session_id=session_id,
                    plan=plan,
                    context=context,
                    trace=trace,
                    ctx_token=ctx_token,
                )
            except Exception:
                request_context.reset(ctx_token)
                raise

        chatbot_id = orchestrator_result.selected_profile_id

    # chatbot_id가 여전히 없으면 에러
    if not chatbot_id:
        raise HTTPException(
            status_code=400,
            detail="chatbot_id가 필요합니다. orchestrator가 비활성 상태입니다.",
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
            orchestrated=orchestrator_result is not None,
            question_len=len(req.question),
            user_id=user_ctx.user_id,
            user_role=user_ctx.user_role,
        )

        profile = await state.profile_store.get(chatbot_id)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile not found: {chatbot_id}")

        # 오케스트레이터 세션 메타 업데이트
        if orchestrator_result:
            await state.session_memory.create_session(
                session_id=session_id,
                profile_id=chatbot_id,
                user_id=user_ctx.user_id,
                ttl_seconds=profile.memory_ttl_seconds,
                tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
            )
            await state.session_memory.update_current_profile(session_id, chatbot_id)

        # 워크플로우 재개 처리
        if orchestrator_result and orchestrator_result.should_resume_workflow:
            paused = orchestrator_result.paused_state
            await state.workflow_engine.resume(
                paused["workflow_id"],
                session_id,
                paused["step_id"],
                paused["collected"],
            )
            # paused_workflow 메타 클리어
            meta = await state.session_memory.get_orchestrator_metadata(session_id)
            meta.pop("paused_workflow", None)
            await state.session_memory.save_orchestrator_metadata(session_id, meta)

        # 활성 워크플로우 세션이 있으면 Router + history 로드 바이패스
        active_wf = await state.workflow_engine.get_session(session_id)
        if active_wf and not active_wf.completed:
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
            if not orchestrator_result:
                await state.session_memory.create_session(
                    session_id=session_id,
                    profile_id=profile.id,
                    user_id=user_ctx.user_id,
                    ttl_seconds=profile.memory_ttl_seconds,
                    tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
                )
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

        return _ChatSetup(
            session_id=session_id,
            plan=plan,
            context=agent_context,
            trace=trace,
            ctx_token=ctx_token,
            profile_id=chatbot_id,
            orchestrated=orchestrator_result is not None,
        )
    except Exception:
        request_context.reset(ctx_token)
        raise


async def _prepare_chat_fast(
    req: ChatRequest,
    request: Request,
    user_ctx: UserContext,
) -> _ChatSetup:
    """chat_stream 전용: 오케스트레이터 호출 없이 즉시 반환.

    세션 메타에서 이전 라우팅 결과(current_profile_id)를 확인하여 재사용.
    없으면 fallback_profile_id 사용.
    오케스트레이터 라우팅은 호출자가 백그라운드에서 별도 실행한다.
    """
    state = _get_app_state(request)
    request_id = str(uuid.uuid4())
    session_id = req.session_id or str(uuid.uuid4())

    # chatbot_id가 명시적으로 전달된 경우: 기존과 동일 (오케스트레이터 바이패스)
    chatbot_id = req.chatbot_id
    needs_routing = False

    if chatbot_id is None:
        # 오케스트레이터가 활성이면 백그라운드 라우팅 예약
        if hasattr(state, "orchestrator") and state.orchestrator:
            needs_routing = True

            # 세션 메타에서 이전 프로필 확인
            try:
                meta = await state.session_memory.get_orchestrator_metadata(session_id)
                chatbot_id = meta.get("current_profile_id")
            except Exception:
                chatbot_id = None

            # 이전 프로필이 없으면 fallback 사용
            if not chatbot_id:
                chatbot_id = state.settings.fallback_profile_id

        if not chatbot_id:
            raise HTTPException(
                status_code=400,
                detail="chatbot_id가 필요합니다. orchestrator가 비활성 상태입니다.",
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
            needs_routing=needs_routing,
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

        # 활성 워크플로우 세션이 있으면 Router + history 로드 바이패스
        active_wf = await state.workflow_engine.get_session(session_id)
        if active_wf and not active_wf.completed:
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

        return _ChatSetup(
            session_id=session_id,
            plan=plan,
            context=agent_context,
            trace=trace,
            ctx_token=ctx_token,
            profile_id=chatbot_id,
            orchestrated=needs_routing,
            needs_routing=needs_routing,
        )
    except Exception:
        request_context.reset(ctx_token)
        raise


# --- 공개 엔드포인트 ---


@gateway_router.get("/health")
async def health(request: Request):
    state = _get_app_state(request)
    return {
        "status": "ok",
        "version": APP_VERSION,
        "provider_mode": state.settings.provider_mode.value,
        "profiles_loaded": state.profile_store.profile_count,
    }


@gateway_router.get("/profiles")
async def list_profiles(request: Request):
    state = _get_app_state(request)
    profiles = await state.profile_store.list_all()
    return [
        {"id": p.id, "name": p.name, "mode": p.mode.value, "domains": p.domain_scopes}
        for p in profiles
    ]


# --- 인증 필수 엔드포인트 ---


@gateway_router.post("/chat", response_model=AgentResponse)
async def chat(req: ChatRequest, request: Request):
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx, sub_key=req.session_id)
    setup: Optional[_ChatSetup] = None

    increment_active()

    request_log_svc = getattr(state, "request_log_service", None)
    cache_svc = getattr(state, "response_cache_service", None)

    # Task 014: 응답 식별자 생성 (api 레이어가 단일 출처)
    response_id = str(uuid.uuid4())
    captured_faithfulness_score: Optional[float] = None

    status_code = 200
    error_code: Optional[str] = None
    cache_hit = False
    response_preview: Optional[str] = None
    profile_for_log: str = ""

    with latency_timer() as timer:
        try:
            setup = await _prepare_chat(req, request, user_ctx)
            profile_for_log = setup.profile_id or ""

            profile = await state.profile_store.get(setup.profile_id) if setup.profile_id else None
            plan_mode = getattr(setup.plan, "mode", None)
            mode_str = plan_mode.value if hasattr(plan_mode, "value") else str(plan_mode or "")
            cacheable = bool(profile) and should_use_cache(profile, mode_str, cache_svc)

            if cacheable:
                cached_text = await try_cache_get(
                    cache_svc, setup.profile_id, mode_str, req.question,
                    tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
                )
                if cached_text is not None:
                    cache_hit = True
                    response_preview = RequestLogEntry.truncate_preview(cached_text)
                    await state.session_memory.add_turn(setup.session_id, "user", req.question)
                    await state.session_memory.add_turn(setup.session_id, "assistant", cached_text)
                    # Task 014: 캐시 응답도 response_id 포함
                    return AgentResponse(answer=cached_text, response_id=response_id)

            response = await state.agent.execute(
                question=req.question,
                plan=setup.plan,
                session_id=setup.session_id,
                trace=setup.trace,
                context=setup.context,
            )

            await state.session_memory.add_turn(setup.session_id, "user", req.question)
            await state.session_memory.add_turn(setup.session_id, "assistant", response.answer)

            if profile and profile.memory_type in ("session", "long"):
                asyncio.create_task(_save_extracted_memories(
                    state=state,
                    tenant_id=user_ctx.user_id,
                    turns=[
                        {"role": "user", "content": req.question},
                        {"role": "assistant", "content": response.answer},
                    ],
                    retention_days=profile.memory_retention_days,
                ))

            setup.trace.log_summary()

            if response.trace:
                response.trace.request_id = setup.trace.request_id
                response.trace.latency_ms = setup.trace.total_ms

            response_preview = RequestLogEntry.truncate_preview(response.answer)
            # Task 014: finally 에서 request_log 에 기록할 점수 캡처
            captured_faithfulness_score = response.guardrail_score

            if cacheable and response.answer:
                await try_cache_put(
                    cache_svc, setup.profile_id, mode_str, req.question, response.answer,
                    tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
                )

            # Task 014: 응답에 response_id 주입 (JSON body)
            response.response_id = response_id
            # guardrail_score 는 내부 전달용 — 클라이언트 응답에서 제거
            response.guardrail_score = None
            return response

        except HTTPException as he:
            status_code = he.status_code
            error_code = f"http_{he.status_code}"
            raise
        except Exception as e:
            status_code = 500
            error_code = "internal_error"
            logger.error("chat_error", error=str(e), exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")
        finally:
            if request_log_svc is not None:
                safe_enqueue(
                    request_log_svc,
                    RequestLogEntry(
                        api_key_id=getattr(user_ctx, "api_key_id", None),
                        profile_id=profile_for_log or None,
                        status_code=status_code,
                        latency_ms=timer["elapsed_ms"],
                        cache_hit=cache_hit,
                        error_code=error_code,
                        request_preview=RequestLogEntry.truncate_preview(req.question),
                        response_preview=response_preview,
                        # Task 014: 응답 식별자 + faithfulness 스코어 영속화
                        response_id=response_id,
                        faithfulness_score=captured_faithfulness_score,
                    ),
                )
            if setup:
                request_context.reset(setup.ctx_token)
            decrement_active()


@gateway_router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx, sub_key=req.session_id)

    increment_active()

    request_log_svc = getattr(state, "request_log_service", None)
    stream_timer_start = time.monotonic()
    # Task 014: 응답 식별자 (api 단일 출처). SSE done 이벤트 + request_log 에 기록.
    response_id = str(uuid.uuid4())

    try:
        setup = await _prepare_chat_fast(req, request, user_ctx)
    except HTTPException as he:
        if request_log_svc is not None:
            elapsed_ms = int((time.monotonic() - stream_timer_start) * 1000)
            safe_enqueue(
                request_log_svc,
                RequestLogEntry(
                    api_key_id=getattr(user_ctx, "api_key_id", None),
                    status_code=he.status_code,
                    latency_ms=elapsed_ms,
                    error_code=f"http_{he.status_code}",
                    request_preview=RequestLogEntry.truncate_preview(req.question),
                    response_id=response_id,
                ),
            )
        decrement_active()
        raise
    except Exception as e:
        logger.error("chat_stream_setup_error", error=str(e), exc_info=True)
        if request_log_svc is not None:
            elapsed_ms = int((time.monotonic() - stream_timer_start) * 1000)
            safe_enqueue(
                request_log_svc,
                RequestLogEntry(
                    api_key_id=getattr(user_ctx, "api_key_id", None),
                    status_code=500,
                    latency_ms=elapsed_ms,
                    error_code="internal_error",
                    request_preview=RequestLogEntry.truncate_preview(req.question),
                    response_id=response_id,
                ),
            )
        decrement_active()
        raise HTTPException(status_code=500, detail="Internal server error")

    # 메모리 추출 대상 프로필 조회
    stream_profile = await state.profile_store.get(setup.profile_id) if setup.profile_id else None

    # 백그라운드 오케스트레이터 라우팅 (스트리밍과 병렬)
    routing_task: Optional[asyncio.Task] = None
    if setup.needs_routing:
        async def _background_route():
            """백그라운드에서 오케스트레이터 라우팅을 실행하고 세션 메타에 결과를 기록한다."""
            try:
                result = await state.orchestrator.route(
                    question=req.question,
                    session_id=setup.session_id,
                    user_ctx=user_ctx,
                )
                if result and result.selected_profile_id:
                    await state.session_memory.update_current_profile(
                        setup.session_id, result.selected_profile_id,
                    )
                logger.info(
                    "background_routing_complete",
                    session_id=setup.session_id,
                    selected_profile=result.selected_profile_id if result else "none",
                    reason=result.reason if result else "none",
                )
            except Exception as exc:
                logger.warning(
                    "background_routing_error",
                    session_id=setup.session_id,
                    error=str(exc),
                )

        routing_task = asyncio.create_task(_background_route())

    # context reset을 generator 종료 시점으로 연기
    async def event_generator():
        gen_status_code = 200
        gen_error_code: Optional[str] = None
        gen_response_preview: Optional[str] = None
        # Task 014: done 이벤트에서 faithfulness_score 포집 (finally enqueue 에 사용)
        captured_faithfulness_score: Optional[float] = None
        try:
            answer_parts = []
            async for event in state.agent.execute_stream(
                question=req.question, plan=setup.plan,
                session_id=setup.session_id, trace=setup.trace,
                context=setup.context,
            ):
                event_type = event["type"]
                if event_type == "thinking":
                    yield {"event": "trace", "data": json.dumps({"step": "thinking", "content": event["data"]}, ensure_ascii=False)}
                elif event_type == "token":
                    answer_parts.append(event["data"])
                    yield {"event": "token", "data": json.dumps({"delta": event["data"]}, ensure_ascii=False)}
                elif event_type == "replace":
                    answer_parts.clear()
                    answer_parts.append(event["data"])
                    yield {"event": "replace", "data": json.dumps({"delta": event["data"]}, ensure_ascii=False)}
                elif event_type == "trace":
                    yield {"event": "trace", "data": json.dumps(event["data"], ensure_ascii=False)}
                elif event_type == "done":
                    done_data = event["data"]
                    done_data["profile_id"] = setup.profile_id
                    done_data["orchestrated"] = setup.orchestrated
                    # KMS 프론트 호환: answer, confidence, traversal_path 필드 추가
                    done_data.setdefault("answer", "".join(answer_parts))
                    done_data.setdefault("confidence", None)
                    done_data.setdefault("traversal_path", [])
                    # Task 014: response_id 주입 + faithfulness_score 캡처
                    done_data["response_id"] = response_id
                    score_value = done_data.get("faithfulness_score")
                    if isinstance(score_value, (int, float)):
                        captured_faithfulness_score = float(score_value)
                    yield {"event": "done", "data": json.dumps(done_data, ensure_ascii=False)}

            full_answer = "".join(answer_parts)
            await state.session_memory.add_turn(setup.session_id, "user", req.question)
            await state.session_memory.add_turn(setup.session_id, "assistant", full_answer)

            if stream_profile and stream_profile.memory_type in ("session", "long"):
                asyncio.create_task(_save_extracted_memories(
                    state=state,
                    tenant_id=user_ctx.user_id,
                    turns=[
                        {"role": "user", "content": req.question},
                        {"role": "assistant", "content": full_answer},
                    ],
                    retention_days=stream_profile.memory_retention_days,
                ))

            setup.trace.log_summary()
            logger.info(
                "stream_complete",
                answer_len=len(full_answer),
                total_ms=round(setup.trace.total_ms, 1),
            )
            gen_response_preview = RequestLogEntry.truncate_preview(full_answer)
        except Exception as stream_err:
            gen_status_code = 500
            gen_error_code = "stream_error"
            logger.error("chat_stream_error", error=str(stream_err), exc_info=True)
            raise
        finally:
            # 백그라운드 라우팅 태스크 정리
            if routing_task and not routing_task.done():
                routing_task.cancel()
                try:
                    await routing_task
                except (asyncio.CancelledError, Exception):
                    pass
            # Request log enqueue — generator 종료 후 (R1 보장)
            if request_log_svc is not None:
                elapsed_ms = int((time.monotonic() - stream_timer_start) * 1000)
                safe_enqueue(
                    request_log_svc,
                    RequestLogEntry(
                        api_key_id=getattr(user_ctx, "api_key_id", None),
                        profile_id=setup.profile_id or None,
                        status_code=gen_status_code,
                        latency_ms=elapsed_ms,
                        error_code=gen_error_code,
                        request_preview=RequestLogEntry.truncate_preview(req.question),
                        response_preview=gen_response_preview,
                        # Task 014: 응답 식별자 + faithfulness 스코어 영속화
                        response_id=response_id,
                        faithfulness_score=captured_faithfulness_score,
                    ),
                )
            # SSE 제너레이터는 별도 Task에서 실행되므로
            # ContextVar 토큰 reset은 안전하게 스킵
            try:
                request_context.reset(setup.ctx_token)
            except ValueError:
                pass  # 다른 Context에서 생성된 토큰
            decrement_active()

    return EventSourceResponse(event_generator())


@gateway_router.post(
    "/documents/ingest",
    response_model=IngestResponse,
    status_code=202,
)
async def ingest_document(req: IngestRequest, request: Request):
    """문서 수집 요청을 큐에 등록하고 job_id를 즉시 반환한다 (202 Accepted)."""
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx)

    # EDITOR 이상만 문서 수집 가능
    if ROLE_LEVELS.get(user_ctx.user_role, 0) < 1:
        raise HTTPException(
            status_code=403,
            detail="문서 수집은 EDITOR 이상 권한이 필요합니다",
        )

    # 통일된 인제스천 계약 — 바이트 획득 방식 두 가지를 한 진입점에서 분기한다:
    #   (1) 인라인: content 또는 file_base64 가 있으면 'ingest' 큐로 (HTTP·챗봇 업로드)
    #   (2) 참조-fetch: source_document_id 만 있으면 'kms_sync' 큐로 (KMS, 파일은 워커가 fetch)
    tenant_id = user_ctx.tenant_id or state.settings.default_tenant_id
    has_inline = bool(req.content or req.file_base64)

    try:
        if has_inline:
            logger.info(
                "ingest_enqueue",
                title=req.title,
                domain_code=req.domain_code,
                content_len=len(req.content) if req.content else 0,
                has_file=bool(req.file_base64),
                user_id=user_ctx.user_id,
            )
            job_id = await state.job_queue.enqueue(
                queue_name="ingest",
                payload={
                    "title": req.title,
                    "content": req.content,
                    "file_base64": req.file_base64,
                    "domain_code": req.domain_code,
                    "file_name": req.file_name,
                    "security_level": req.security_level,
                    "source_url": req.source_url,
                    "metadata": req.metadata or {},
                    "source_document_id": req.source_document_id,
                    "mime_type": req.mime_type,
                    # 테넌트 격리(A2): 키에 테넌트 없으면 기본 테넌트로 스탬핑
                    "tenant_id": tenant_id,
                },
            )
        elif req.source_document_id:
            # 참조-fetch: 동작하는 KMS sync 경로 재사용 (워커가 KMS에서 파일을 받아 파싱)
            logger.info(
                "ingest_enqueue_source_ref",
                title=req.title,
                domain_code=req.domain_code,
                source_document_id=req.source_document_id,
                source_system=req.source_system or "kms",
                user_id=user_ctx.user_id,
            )
            job_id = await state.job_queue.enqueue(
                queue_name="kms_sync",
                payload={
                    "action": "sync",
                    "document_id": req.source_document_id,
                    "event": "ingest.source_ref",
                    "data": {"domainCodes": [req.domain_code] if req.domain_code else []},
                },
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="content, file_base64, or source_document_id required",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("ingest_enqueue_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    return IngestResponse(job_id=job_id, status="queued")


@gateway_router.post("/chat/sessions/{session_id}/files", status_code=202)
async def upload_session_file(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    """챗봇 세션에 파일을 업로드해 적재한다.

    새 진입점이지만 코어(IngestPipeline)·워커 변경 0 — 기존 'ingest' 큐를 그대로
    재사용한다. 업로드 문서는 `metadata.session_id`와 `external_id=session:{id}:{uuid}`
    로 태깅되어, 세션 스코프 검색/만료 정리의 연결점이 된다.
    """
    import base64

    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx)

    # 업로드 문서는 RAG 스토어에 적재된다(세션 스코프 검색은 추후) — 지식베이스
    # 오염을 막기 위해 ingest_document 와 동일하게 EDITOR 이상으로 제한한다.
    if ROLE_LEVELS.get(user_ctx.user_role, 0) < 1:
        raise HTTPException(status_code=403, detail="세션 파일 업로드는 EDITOR 이상 권한이 필요합니다")

    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid session_id: {session_id}")

    tenant_id = user_ctx.tenant_id or state.settings.default_tenant_id

    # 세션 소유권 검증 (IDOR 방지): 세션이 호출자(테넌트+유저)의 것인지 확인한다.
    # 타 세션 주입을 막고, 열거(enumeration) 방지를 위해 미존재/미소유 모두 404.
    session = await state.session_memory.get_session(session_id, tenant_id=tenant_id)
    if not session or (session.get("user_id") and session["user_id"] != user_ctx.user_id):
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="빈 파일입니다")

    external_id = f"session:{session_id}:{uuid.uuid4().hex}"

    logger.info(
        "session_file_upload",
        session_id=session_id,
        file_name=file.filename,
        size=len(file_bytes),
        user_id=user_ctx.user_id,
    )

    try:
        job_id = await state.job_queue.enqueue(
            queue_name="ingest",
            payload={
                "title": file.filename or "uploaded-file",
                "file_base64": base64.b64encode(file_bytes).decode(),
                "domain_code": "",
                "file_name": file.filename,
                "security_level": "PUBLIC",
                "metadata": {"session_id": session_id, "source": "chat_upload"},
                "source_document_id": external_id,
                "mime_type": file.content_type,
                "tenant_id": tenant_id,
            },
        )
    except Exception as e:
        logger.error("session_upload_enqueue_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    # 세션 메타에 업로드 external_id 추적 — 검색 스코핑/만료 정리의 연결점.
    try:
        meta = await state.session_memory.get_orchestrator_metadata(session_id) or {}
        uploads = list(meta.get("uploaded_external_ids", []))
        uploads.append(external_id)
        meta["uploaded_external_ids"] = uploads
        await state.session_memory.save_orchestrator_metadata(session_id, meta)
    except Exception as e:
        logger.warning("session_upload_meta_link_failed", session_id=session_id, error=str(e))

    return {"job_id": job_id, "status": "queued", "external_id": external_id}


@gateway_router.get("/documents/ingest/{job_id}", response_model=IngestJobStatus)
async def get_ingest_status(job_id: str, request: Request):
    """문서 수집 작업 상태를 조회한다 (폴링 엔드포인트)."""
    state = _get_app_state(request)
    await _authenticate(request)

    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid job_id format: {job_id}")

    try:
        job = await state.job_queue.get_job(job_id)
    except Exception as e:
        logger.error("ingest_status_error", error=str(e), job_id=job_id)
        raise HTTPException(status_code=500, detail="Internal server error")

    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    # pending -> queued (외부 API 용어 통일)
    status = "queued" if job["status"] == "pending" else job["status"]
    result = job.get("result") if job["status"] == "completed" else None

    return IngestJobStatus(
        job_id=job["id"],
        status=status,
        result=result,
        error=job.get("last_error"),
        attempts=job["attempts"],
        created_at=job.get("created_at"),
    )


# --- 워크플로우 (순차적 챗봇) ---


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


@gateway_router.get("/workflows")
async def list_workflows(request: Request):
    """사용 가능한 워크플로우 목록."""
    state = _get_app_state(request)
    workflows = await state.workflow_store.list_all_async()
    return [
        {"id": w.id, "name": w.name, "steps": len(w.steps)}
        for w in workflows
    ]


@gateway_router.post("/workflow/start")
async def workflow_start(req: WorkflowStartRequest, request: Request):
    """워크플로우를 시작하고 첫 번째 스텝을 반환한다."""
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)

    session_id = req.session_id or str(uuid.uuid4())

    logger.info(
        "workflow_start_request",
        workflow_id=req.workflow_id,
        session_id=session_id,
        user_id=user_ctx.user_id,
    )

    try:
        result = await state.workflow_engine.start(req.workflow_id, session_id)
    except Exception as e:
        logger.error("workflow_start_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

    response = _step_to_response(result)
    response["session_id"] = session_id
    response["workflow_id"] = req.workflow_id
    return response


@gateway_router.post("/workflow/advance")
async def workflow_advance(req: WorkflowAdvanceRequest, request: Request):
    """사용자 입력을 받아 다음 스텝으로 진행한다."""
    state = _get_app_state(request)
    await _authenticate(request)

    try:
        result = await state.workflow_engine.advance(req.session_id, req.input)
    except Exception as e:
        logger.error("workflow_advance_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

    return _step_to_response(result)


# --- API Key 관리 (ADMIN 전용) ---


@gateway_router.post("/api-keys")
async def create_api_key(request: Request):
    """새 API Key를 생성한다. ADMIN만 가능."""
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    if user_ctx.user_role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="ADMIN 권한이 필요합니다")

    body = await request.json()
    name = body.get("name", "unnamed")
    user_role = body.get("user_role", UserRole.VIEWER)
    security_level_max = body.get("security_level_max", "PUBLIC")
    allowed_profiles = body.get("allowed_profiles", [])
    allowed_origins = body.get("allowed_origins", [])
    rate_limit = body.get("rate_limit_per_min", 60)
    user_type = body.get("user_type", "")
    key_type = body.get("key_type", "secret")

    try:
        raw_key, key_hash = await state.auth_service.create_key(
            name=name,
            creator_user_id=user_ctx.user_id,
            user_role=user_role,
            security_level_max=security_level_max,
            allowed_profiles=allowed_profiles,
            allowed_origins=allowed_origins,
            rate_limit_per_min=rate_limit,
            user_type=user_type,
            key_type=key_type,
        )
    except AuthError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "api_key": raw_key,
        "name": name,
        "key_type": key_type,
        "user_role": user_role,
        "security_level_max": security_level_max,
        "allowed_origins": allowed_origins,
        "message": "이 키는 다시 표시되지 않습니다. 안전하게 보관하세요.",
    }


# --- 피드백 (Task 014) ---


@gateway_router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(req: FeedbackRequest, request: Request):
    """응답 피드백을 저장한다 (JWT 또는 API Key 인증 필수).

    - (user_id, response_id) 조합이 이미 존재하면 upsert (score/comment 갱신)
    - 저장 실패 시 500 반환 (조용히 삼키지 않는다)
    """
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)

    feedback_svc = getattr(state, "feedback_service", None)
    if feedback_svc is None:
        raise HTTPException(status_code=503, detail="feedback service not initialized")

    if not user_ctx.user_id:
        raise HTTPException(status_code=401, detail="user_id missing in auth context")

    try:
        return await feedback_svc.submit(user_ctx.user_id, req)
    except Exception as e:
        logger.error("feedback_submit_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="피드백 저장에 실패했습니다.")


@gateway_router.get("/admin/feedback", response_model=AdminFeedbackPage)
async def list_admin_feedback(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    only_negative: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """관리자용 피드백 리스트 조회 (JOIN api_request_logs).

    - limit max 200, offset >= 0
    - only_negative=true → score=-1 만
    - date_from/date_to: ISO8601
    - ADMIN 권한 필요
    """
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)

    if user_ctx.user_role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="ADMIN 권한이 필요합니다")

    feedback_svc = getattr(state, "feedback_service", None)
    if feedback_svc is None:
        raise HTTPException(status_code=503, detail="feedback service not initialized")

    from datetime import datetime as _dt

    def _parse_iso(v: Optional[str]) -> Optional[object]:
        if not v:
            return None
        try:
            # Python 3.11 fromisoformat 은 Z 를 처리하지 못하므로 치환
            return _dt.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=f"invalid date: {v}") from ex

    df = _parse_iso(date_from)
    dt_ = _parse_iso(date_to)

    try:
        return await feedback_svc.list_for_admin(
            limit=limit,
            offset=offset,
            only_negative=only_negative,
            date_from=df,
            date_to=dt_,
        )
    except Exception as e:
        logger.error("feedback_list_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="피드백 조회에 실패했습니다.")


# --- 세션 / 히스토리 ---


@gateway_router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    request: Request,
    profile_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
):
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)

    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    items, total = await state.session_memory.list_sessions(
        user_id=user_ctx.user_id,
        profile_id=profile_id,
        limit=limit,
        offset=offset,
        tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
    )

    return SessionListResponse(
        sessions=[
            SessionListItem(
                session_id=s["id"],
                profile_id=s["profile_id"] or "",
                created_at=s["created_at"].isoformat() if s.get("created_at") else "",
                updated_at=s["updated_at"].isoformat() if s.get("updated_at") else "",
                turn_count=s.get("turn_count", 0),
            )
            for s in items
        ],
        total=total,
    )


@gateway_router.get("/sessions/{session_id}/history", response_model=SessionHistoryResponse)
async def get_session_history(
    session_id: str,
    request: Request,
    max_turns: int = 50,
):
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)

    session = await state.session_memory.get_session(
        session_id, tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("user_id") != user_ctx.user_id and user_ctx.user_role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    max_turns = max(1, min(max_turns, 200))
    turns = await state.session_memory.get_turns(session_id, max_turns=max_turns)

    return SessionHistoryResponse(
        session_id=session_id,
        profile_id=session.get("profile_id") or "",
        turns=turns,
        created_at=session["created_at"].isoformat() if session.get("created_at") else "",
        updated_at=session["updated_at"].isoformat() if session.get("updated_at") else "",
    )
