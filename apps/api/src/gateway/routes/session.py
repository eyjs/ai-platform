"""세션 / 히스토리 엔드포인트: /sessions, /sessions/{id}/history."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from src.domain.models import UserRole
from src.gateway.models import (
    SessionHistoryResponse,
    SessionListItem,
    SessionListResponse,
)
from src.gateway.routes.helpers import _authenticate, _get_app_state

router = APIRouter()


@router.get("/sessions", response_model=SessionListResponse)
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


@router.get("/sessions/{session_id}/history", response_model=SessionHistoryResponse)
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
