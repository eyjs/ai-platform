"""역방향 분석 엔드포인트 (ADMIN 전용).

RAG 트레이스에 남은 chunk_id/document_id 로 근거를 역추적한다:
  청크 → 전문·섹션·소속 문서 (청크 뷰어)
  문서 → 메타 + 청크 순서 재조립 (원본 문서 뷰어; AST-lite 원본 복원 축)
"""

import uuid as _uuid

from fastapi import APIRouter, HTTPException, Query, Request

from src.domain.models import UserRole
from src.gateway.routes.helpers import _authenticate, _get_app_state

router = APIRouter()

MAX_PAGE_LIMIT = 500


async def _require_admin(request: Request):
    user_ctx = await _authenticate(request)
    if user_ctx.user_role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="ADMIN 권한이 필요합니다")
    return user_ctx


def _validate_uuid(value: str, label: str) -> None:
    try:
        _uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 {label}입니다")


@router.get("/admin/chunks/{chunk_id}")
async def get_chunk_detail(chunk_id: str, request: Request):
    """청크 역조회 — 전문 + 섹션 메타 + 소속 문서."""
    await _require_admin(request)
    _validate_uuid(chunk_id, "chunk_id")
    state = _get_app_state(request)
    detail = await state.vector_store.get_chunk_detail(chunk_id)
    if not detail:
        raise HTTPException(status_code=404, detail="청크를 찾을 수 없습니다")
    return detail


@router.get("/admin/documents/{document_id}")
async def get_document_meta(document_id: str, request: Request):
    """문서 메타 + 청크 수 (원본 문서 뷰어 헤더)."""
    await _require_admin(request)
    _validate_uuid(document_id, "document_id")
    state = _get_app_state(request)
    meta = await state.vector_store.get_document_meta(document_id)
    if not meta:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")
    return meta


@router.get("/admin/documents/{document_id}/chunks")
async def get_document_chunks(
    document_id: str,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=MAX_PAGE_LIMIT),
):
    """문서 청크 페이지 조회 — chunk_index 순 (원본 복원 뷰어 본문)."""
    await _require_admin(request)
    _validate_uuid(document_id, "document_id")
    state = _get_app_state(request)
    chunks = await state.vector_store.get_document_chunks_page(
        document_id, offset=offset, limit=limit,
    )
    return {"document_id": document_id, "offset": offset, "chunks": chunks}
