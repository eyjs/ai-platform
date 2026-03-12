"""AI Gateway: FastAPI 엔드포인트.

/chat/stream, /chat, /documents/ingest, /profiles, /health
"""

import json
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from src.domain.models import AgentResponse, UserRole
from src.gateway.models import ChatRequest, IngestRequest, IngestResponse
from src.observability.logging import RequestContext, get_logger, request_context
from src.observability.trace_logger import RequestTrace
from src.tools.base import AgentContext

APP_VERSION = "0.1.0"

logger = get_logger(__name__)

gateway_router = APIRouter()


def _get_app_state(request: Request):
    """FastAPI app.state에서 컴포넌트를 가져온다."""
    return request.app.state


@dataclass
class _ChatSetup:
    """chat/chat_stream 공통 세팅 결과."""

    session_id: str
    plan: object  # ExecutionPlan
    context: AgentContext
    trace: RequestTrace
    ctx_token: object  # contextvars.Token


async def _prepare_chat(req: ChatRequest, request: Request) -> _ChatSetup:
    """chat/chat_stream 공통 로직: Profile 로딩 -> 세션 -> history -> Router."""
    state = _get_app_state(request)
    request_id = str(uuid.uuid4())
    session_id = req.session_id or str(uuid.uuid4())

    ctx_token = request_context.set(RequestContext(
        request_id=request_id,
        session_id=session_id,
        profile_id=req.chatbot_id,
        user_id=req.user_id or "",
    ))

    try:
        logger.info(
            "chat_request",
            question=req.question[:100],
            chatbot_id=req.chatbot_id,
            question_len=len(req.question),
        )

        profile = await state.profile_store.get(req.chatbot_id)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile not found: {req.chatbot_id}")

        await state.session_memory.create_session(
            session_id=session_id,
            profile_id=profile.id,
            user_id=req.user_id or "",
            ttl_seconds=profile.memory_ttl_seconds,
        )
        history = await state.session_memory.get_turns(session_id, max_turns=10)

        tools = state.tool_registry.resolve(profile.tool_names)
        plan = await state.ai_router.route(
            query=req.question,
            profile=profile,
            tools=tools,
            history=history,
            user_security_level=req.user_role or UserRole.VIEWER,
        )

        context = AgentContext(
            session_id=session_id,
            user_id=req.user_id or "",
            user_role=req.user_role or UserRole.VIEWER,
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
        {"id": p.id, "name": p.name, "mode": p.mode.value, "domains": p.domain_scopes}  # noqa: E501
        for p in profiles
    ]


@gateway_router.post("/chat", response_model=AgentResponse)
async def chat(req: ChatRequest, request: Request):
    state = _get_app_state(request)
    setup: Optional[_ChatSetup] = None

    try:
        setup = await _prepare_chat(req, request)

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

    try:
        setup = await _prepare_chat(req, request)
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
            request_context.reset(setup.ctx_token)

    return EventSourceResponse(event_generator())


@gateway_router.post("/documents/ingest", response_model=IngestResponse)
async def ingest_document(req: IngestRequest, request: Request):
    state = _get_app_state(request)
    request_id = str(uuid.uuid4())

    ctx_token = request_context.set(RequestContext(
        request_id=request_id,
        profile_id="ingest",
    ))

    try:
        logger.info(
            "ingest_request",
            title=req.title,
            domain_code=req.domain_code,
            content_len=len(req.content) if req.content else 0,
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
