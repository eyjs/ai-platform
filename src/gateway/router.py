"""AI Gateway: FastAPI 엔드포인트.

/chat/stream, /chat, /documents/ingest, /profiles, /health

인증:
- /health, /profiles -> 공개 (인증 불필요)
- /chat, /chat/stream, /documents/ingest -> 인증 필수 (JWT 또는 API Key)
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from src.domain.models import AgentMode, AgentResponse, SearchScope, UserRole
from src.gateway.auth import AuthError
from src.gateway.models import (
    ChatRequest, IngestJobStatus, IngestRequest, IngestResponse, UserContext,
    WorkflowAdvanceRequest, WorkflowStartRequest,
)
from src.observability.logging import RequestContext, get_logger, request_context
from src.orchestrator.models import OrchestratorResult
from src.router.execution_plan import ExecutionPlan
from src.workflow.engine import StepResult
from src.observability.trace_logger import RequestTrace
from src.domain.agent_context import AgentContext

APP_VERSION = "0.1.0"

ROLE_LEVELS = {"VIEWER": 0, "EDITOR": 1, "REVIEWER": 2, "APPROVER": 3, "ADMIN": 4}

logger = get_logger(__name__)

gateway_router = APIRouter()


def _get_app_state(request: Request):
    """FastAPI app.state에서 컴포넌트를 가져온다."""
    return request.app.state


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

    return user_ctx


async def _check_rate_limit(request: Request, user_ctx: UserContext) -> None:
    """Token Bucket Rate Limiting. UserContext.rate_limit_per_min 기반."""
    state = _get_app_state(request)
    client_id = user_ctx.user_id or request.client.host
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
            )
            await state.session_memory.update_current_profile(session_id, chatbot_id)

        # 워크플로우 재개 처리
        if orchestrator_result and orchestrator_result.should_resume_workflow:
            paused = orchestrator_result.paused_state
            state.workflow_engine.resume(
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
        active_wf = state.workflow_engine.get_session(session_id)
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
                )
            history = await state.session_memory.get_turns(session_id, max_turns=10)

            skip_context_resolve = req.chatbot_id is not None

            tools = state.tool_registry.resolve(profile.tool_names)
            plan = await state.ai_router.route(
                query=req.question,
                profile=profile,
                tools=tools,
                history=history,
                user_security_level=user_ctx.security_level_max,
                skip_context_resolve=skip_context_resolve,
                external_context=req.context or "",
            )

        agent_context = AgentContext(
            session_id=session_id,
            user_id=user_ctx.user_id,
            user_role=user_ctx.user_role,
            conversation_history=history,
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
        )

        # 활성 워크플로우 세션이 있으면 Router + history 로드 바이패스
        active_wf = state.workflow_engine.get_session(session_id)
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
            history = await state.session_memory.get_turns(session_id, max_turns=10)

            # chatbot_id가 명시적으로 전달된 경우: L0 ContextResolver를 건너뛴다
            # 이미 특정 챗봇을 지정했으므로 대명사 해소/질문 재작성이 불필요
            skip_context_resolve = req.chatbot_id is not None

            tools = state.tool_registry.resolve(profile.tool_names)
            plan = await state.ai_router.route(
                query=req.question,
                profile=profile,
                tools=tools,
                history=history,
                user_security_level=user_ctx.security_level_max,
                skip_context_resolve=skip_context_resolve,
                external_context=req.context or "",
            )

        agent_context = AgentContext(
            session_id=session_id,
            user_id=user_ctx.user_id,
            user_role=user_ctx.user_role,
            conversation_history=history,
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
    await _check_rate_limit(request, user_ctx)
    setup: Optional[_ChatSetup] = None

    try:
        setup = await _prepare_chat(req, request, user_ctx)

        response = await state.agent.execute(
            question=req.question,
            plan=setup.plan,
            session_id=setup.session_id,
            trace=setup.trace,
        )

        await state.session_memory.add_turn(setup.session_id, "user", req.question)
        await state.session_memory.add_turn(setup.session_id, "assistant", response.answer)

        setup.trace.log_summary()

        if response.trace:
            response.trace.request_id = setup.trace.request_id
            response.trace.latency_ms = setup.trace.total_ms

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("chat_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if setup:
            request_context.reset(setup.ctx_token)


@gateway_router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx)

    try:
        setup = await _prepare_chat_fast(req, request, user_ctx)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("chat_stream_setup_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

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
        try:
            answer_parts = []
            async for event in state.agent.execute_stream(
                question=req.question, plan=setup.plan,
                session_id=setup.session_id, trace=setup.trace,
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
                    yield {"event": "done", "data": json.dumps(done_data, ensure_ascii=False)}

            full_answer = "".join(answer_parts)
            await state.session_memory.add_turn(setup.session_id, "user", req.question)
            await state.session_memory.add_turn(setup.session_id, "assistant", full_answer)

            setup.trace.log_summary()
            logger.info(
                "stream_complete",
                answer_len=len(full_answer),
                total_ms=round(setup.trace.total_ms, 1),
            )
        finally:
            # 백그라운드 라우팅 태스크 정리
            if routing_task and not routing_task.done():
                routing_task.cancel()
                try:
                    await routing_task
                except (asyncio.CancelledError, Exception):
                    pass
            # SSE 제너레이터는 별도 Task에서 실행되므로
            # ContextVar 토큰 reset은 안전하게 스킵
            try:
                request_context.reset(setup.ctx_token)
            except ValueError:
                pass  # 다른 Context에서 생성된 토큰

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

    if not req.content and not req.source_url:
        raise HTTPException(status_code=400, detail="content or source_url required")

    if req.source_url and not req.content:
        raise HTTPException(status_code=501, detail="URL ingest not yet implemented")

    logger.info(
        "ingest_enqueue",
        title=req.title,
        domain_code=req.domain_code,
        content_len=len(req.content) if req.content else 0,
        user_id=user_ctx.user_id,
    )

    try:
        job_id = await state.job_queue.enqueue(
            queue_name="ingest",
            payload={
                "title": req.title,
                "content": req.content,
                "domain_code": req.domain_code,
                "file_name": req.file_name,
                "security_level": req.security_level,
                "source_url": req.source_url,
                "metadata": req.metadata or {},
            },
        )
    except Exception as e:
        logger.error("ingest_enqueue_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    return IngestResponse(job_id=job_id, status="queued")


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
        result = state.workflow_engine.start(req.workflow_id, session_id)
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
        result = state.workflow_engine.advance(req.session_id, req.input)
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
        )
    except AuthError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "api_key": raw_key,
        "name": name,
        "user_role": user_role,
        "security_level_max": security_level_max,
        "allowed_origins": allowed_origins,
        "message": "이 키는 다시 표시되지 않습니다. 안전하게 보관하세요.",
    }
