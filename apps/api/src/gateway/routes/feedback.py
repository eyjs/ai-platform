"""피드백 엔드포인트: /feedback, /admin/feedback (Task 014)."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from src.domain.models import UserRole
from src.gateway.routes.helpers import _authenticate, _get_app_state, logger
from src.services.feedback_models import (
    AdminFeedbackPage,
    FeedbackRequest,
    FeedbackResponse,
)

router = APIRouter()


@router.post("/feedback", response_model=FeedbackResponse)
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


@router.get("/admin/feedback", response_model=AdminFeedbackPage)
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
