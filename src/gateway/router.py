"""AI Gateway: FastAPI 엔드포인트.

/chat/stream, /chat, /documents/ingest, /profiles, /health

인증:
- /health, /profiles → 공개 (인증 불필요)
- /chat, /chat/stream, /documents/ingest → 인증 필수 (JWT 또는 API Key)
"""

import json
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from src.domain.models import AgentResponse, UserRole
from src.gateway.auth import AuthError
from src.gateway.models import (
    ChatRequest, IngestRequest, IngestResponse, UserContext,
    WorkflowAdvanceRequest, WorkflowStartRequest,
)
from src.observability.logging import RequestContext, get_logger, request_context
from src.workflow.engine import StepResult
from src.observability.trace_logger import RequestTrace
from src.tools.base import AgentContext

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


@dataclass
class _ChatSetup:
    """chat/chat_stream 공통 세팅 결과."""

    session_id: str
    plan: object  # ExecutionPlan
    context: AgentContext
    trace: RequestTrace
    ctx_token: object  # contextvars.Token


async def _prepare_chat(
    req: ChatRequest,
    request: Request,
    user_ctx: UserContext,
) -> _ChatSetup:
    """chat/chat_stream 공통 로직: 인증 → Profile 로딩 → 세션 → history → Router."""
    state = _get_app_state(request)
    request_id = str(uuid.uuid4())
    session_id = req.session_id or str(uuid.uuid4())

    ctx_token = request_context.set(RequestContext(
        request_id=request_id,
        session_id=session_id,
        profile_id=req.chatbot_id,
        user_id=user_ctx.user_id,
    ))

    try:
        # 프로필 접근 권한 확인
        try:
            await state.auth_service.check_profile_access(user_ctx, req.chatbot_id)
        except AuthError as e:
            raise HTTPException(status_code=403, detail=str(e))

        logger.info(
            "chat_request",
            question=req.question[:100],
            chatbot_id=req.chatbot_id,
            question_len=len(req.question),
            user_id=user_ctx.user_id,
            user_role=user_ctx.user_role,
        )

        profile = await state.profile_store.get(req.chatbot_id)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile not found: {req.chatbot_id}")

        await state.session_memory.create_session(
            session_id=session_id,
            profile_id=profile.id,
            user_id=user_ctx.user_id,
            ttl_seconds=profile.memory_ttl_seconds,
        )
        history = await state.session_memory.get_turns(session_id, max_turns=10)

        tools = state.tool_registry.resolve(profile.tool_names)
        plan = await state.ai_router.route(
            query=req.question,
            profile=profile,
            tools=tools,
            history=history,
            user_security_level=user_ctx.user_role,
        )

        context = AgentContext(
            session_id=session_id,
            user_id=user_ctx.user_id,
            user_role=user_ctx.user_role,
            conversation_history=history,
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

    try:
        setup = await _prepare_chat(req, request, user_ctx)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("chat_stream_setup_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    # context reset을 generator 종료 시점으로 연기
    async def event_generator():
        try:
            answer_parts = []
            async for event in state.agent.execute_stream(
                question=req.question, plan=setup.plan,
                session_id=setup.session_id, trace=setup.trace,
            ):
                event_type = event["type"]
                if event_type == "token":
                    answer_parts.append(event["data"])
                    yield {"event": "token", "data": event["data"]}
                elif event_type == "replace":
                    answer_parts.clear()
                    answer_parts.append(event["data"])
                    yield {"event": "replace", "data": event["data"]}
                elif event_type == "trace":
                    yield {"event": "trace", "data": json.dumps(event["data"], ensure_ascii=False)}
                elif event_type == "done":
                    yield {"event": "done", "data": json.dumps(event["data"], ensure_ascii=False)}

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
            # SSE 제너레이터는 별도 Task에서 실행되므로
            # ContextVar 토큰 reset은 안전하게 스킵
            try:
                request_context.reset(setup.ctx_token)
            except ValueError:
                pass  # 다른 Context에서 생성된 토큰

    return EventSourceResponse(event_generator())


@gateway_router.post("/documents/ingest", response_model=IngestResponse)
async def ingest_document(req: IngestRequest, request: Request):
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)

    # EDITOR 이상만 문서 수집 가능
    if ROLE_LEVELS.get(user_ctx.user_role, 0) < 1:
        raise HTTPException(
            status_code=403,
            detail="문서 수집은 EDITOR 이상 권한이 필요합니다",
        )

    request_id = str(uuid.uuid4())
    ctx_token = request_context.set(RequestContext(
        request_id=request_id,
        profile_id="ingest",
        user_id=user_ctx.user_id,
    ))

    try:
        logger.info(
            "ingest_request",
            title=req.title,
            domain_code=req.domain_code,
            content_len=len(req.content) if req.content else 0,
            user_id=user_ctx.user_id,
        )

        if not req.content and not req.source_url:
            raise HTTPException(status_code=400, detail="content or source_url required")

        content = req.content or ""
        if req.source_url and not content:
            raise HTTPException(status_code=501, detail="URL ingest not yet implemented")

        start = time.time()
        result = await state.ingest_pipeline.ingest_text(
            title=req.title,
            content=content,
            domain_code=req.domain_code,
            file_name=req.file_name,
            security_level=req.security_level,
            source_url=req.source_url,
            metadata=req.metadata,
        )
        elapsed = (time.time() - start) * 1000

        logger.info(
            "ingest_complete",
            document_id=result["document_id"],
            chunks=result["chunks"],
            latency_ms=round(elapsed, 1),
        )

        return IngestResponse(
            document_id=result["document_id"],
            chunks=result["chunks"],
            status=result["status"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("ingest_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        request_context.reset(ctx_token)


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
    return [
        {"id": w.id, "name": w.name, "steps": len(w.steps)}
        for w in state.workflow_store.list_all()
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

    try:
        raw_key, key_hash = await state.auth_service.create_key(
            name=name,
            creator_user_id=user_ctx.user_id,
            user_role=user_role,
            security_level_max=security_level_max,
            allowed_profiles=allowed_profiles,
            allowed_origins=allowed_origins,
            rate_limit_per_min=rate_limit,
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
