"""AI Gateway: FastAPI 엔드포인트.

/chat/stream, /chat, /documents/ingest, /profiles, /health
"""

import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from src.gateway.models import ChatRequest, ChatResponse, IngestRequest, IngestResponse
from src.tools.base import AgentContext

logger = logging.getLogger(__name__)

gateway_router = APIRouter()


def _get_app_state(request: Request):
    """FastAPI app.state에서 컴포넌트를 가져온다."""
    return request.app.state


@gateway_router.get("/health")
async def health(request: Request):
    state = _get_app_state(request)
    return {
        "status": "ok",
        "version": "0.1.0",
        "provider_mode": state.settings.provider_mode.value,
        "profiles_loaded": len(state.profile_store._cache),
    }


@gateway_router.get("/profiles")
async def list_profiles(request: Request):
    state = _get_app_state(request)
    profiles = await state.profile_store.list_all()
    return [
        {"id": p.id, "name": p.name, "mode": p.mode, "domains": p.domain_scopes}
        for p in profiles
    ]


@gateway_router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    state = _get_app_state(request)

    # Profile 로딩
    profile = await state.profile_store.get(req.chatbot_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile not found: {req.chatbot_id}")

    session_id = req.session_id or str(uuid.uuid4())

    # 세션 생성/갱신
    await state.session_memory.create_session(
        session_id=session_id,
        profile_id=profile.id,
        user_id=req.user_id or "",
        ttl_seconds=profile.memory_ttl_seconds,
    )

    # 대화 이력
    history = await state.session_memory.get_turns(session_id, max_turns=10)

    # Router 4-Layer
    tools = state.tool_registry.resolve(profile)
    plan = await state.ai_router.route(
        query=req.question,
        profile=profile,
        tools=tools,
        history=history,
        user_security_level=req.user_role or "VIEWER",
    )

    # Agent 실행
    context = AgentContext(
        session_id=session_id,
        user_id=req.user_id or "",
        user_role=req.user_role or "VIEWER",
        conversation_history=history,
    )
    response = await state.agent.execute(req.question, plan, context)

    # 대화 턴 저장
    await state.session_memory.add_turn(session_id, "user", req.question)
    await state.session_memory.add_turn(session_id, "assistant", response.answer)

    return response


@gateway_router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    state = _get_app_state(request)

    profile = await state.profile_store.get(req.chatbot_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Profile not found: {req.chatbot_id}")

    session_id = req.session_id or str(uuid.uuid4())

    await state.session_memory.create_session(
        session_id=session_id,
        profile_id=profile.id,
        user_id=req.user_id or "",
        ttl_seconds=profile.memory_ttl_seconds,
    )

    history = await state.session_memory.get_turns(session_id, max_turns=10)
    tools = state.tool_registry.resolve(profile)
    plan = await state.ai_router.route(
        query=req.question,
        profile=profile,
        tools=tools,
        history=history,
        user_security_level=req.user_role or "VIEWER",
    )

    context = AgentContext(
        session_id=session_id,
        user_id=req.user_id or "",
        user_role=req.user_role or "VIEWER",
        conversation_history=history,
    )

    async def event_generator():
        answer_parts = []
        async for event in state.agent.execute_stream(req.question, plan, context):
            event_type = event["type"]
            if event_type == "token":
                answer_parts.append(event["data"])
                yield {"event": "token", "data": event["data"]}
            elif event_type == "trace":
                yield {"event": "trace", "data": json.dumps(event["data"], ensure_ascii=False)}
            elif event_type == "done":
                yield {"event": "done", "data": json.dumps(event["data"], ensure_ascii=False)}

        # 대화 턴 저장
        full_answer = "".join(answer_parts)
        await state.session_memory.add_turn(session_id, "user", req.question)
        await state.session_memory.add_turn(session_id, "assistant", full_answer)

    return EventSourceResponse(event_generator())


@gateway_router.post("/documents/ingest", response_model=IngestResponse)
async def ingest_document(req: IngestRequest, request: Request):
    state = _get_app_state(request)

    if not req.content and not req.source_url:
        raise HTTPException(status_code=400, detail="content or source_url required")

    content = req.content or ""

    # URL 수집은 향후 구현
    if req.source_url and not content:
        raise HTTPException(status_code=501, detail="URL ingest not yet implemented")

    result = await state.ingest_pipeline.ingest_text(
        title=req.title,
        content=content,
        domain_code=req.domain_code,
        file_name=req.file_name,
        security_level=req.security_level,
        source_url=req.source_url,
        metadata=req.metadata,
    )

    return IngestResponse(
        document_id=result["document_id"],
        chunks=result["chunks"],
        status=result["status"],
    )
