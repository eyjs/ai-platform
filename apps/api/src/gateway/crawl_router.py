"""크롤링 API 라우터.

KMS에서 프록시하는 크롤링 요청을 수신하고 비동기 Job 큐에 등록한다.
크롤링 결과는 webhook으로 KMS에 콜백한다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.gateway.auth import AuthError
from src.gateway.models import UserContext
from src.observability.logging import get_logger

logger = get_logger(__name__)

crawl_router = APIRouter(prefix="/crawl", tags=["crawl"])

ROLE_LEVELS = {"VIEWER": 0, "EDITOR": 1, "REVIEWER": 2, "APPROVER": 3, "ADMIN": 4}


class CrawlRequest(BaseModel):
    """KMS에서 전달하는 크롤링 요청."""

    request_id: str
    url: str
    instructions: str | None = None
    target_domain: str | None = None
    spider_mode: bool = False


class CrawlCancelRequest(BaseModel):
    """크롤링 취소 요청."""
    pass


class GenerateInstructionsRequest(BaseModel):
    """작업지시서 생성 요청."""

    description: str


async def _authenticate_crawl(request: Request) -> UserContext:
    """API Key 인증 (KMS 내부 통신용)."""
    state = request.app.state
    auth_service = state.auth_service
    try:
        return await auth_service.authenticate(
            authorization=request.headers.get("Authorization"),
            api_key=request.headers.get("X-API-Key"),
        )
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))


@crawl_router.post("")
async def start_crawl(req: CrawlRequest, request: Request):
    """크롤링 작업을 큐에 등록한다."""
    user_ctx = await _authenticate_crawl(request)

    if ROLE_LEVELS.get(user_ctx.user_role, 0) < 1:
        raise HTTPException(status_code=403, detail="크롤링은 EDITOR 이상 권한이 필요합니다")

    state = request.app.state

    logger.info(
        "crawl_enqueue",
        request_id=req.request_id,
        url=req.url,
        spider_mode=req.spider_mode,
    )

    try:
        job_id = await state.job_queue.enqueue(
            queue_name="crawl",
            payload={
                "request_id": req.request_id,
                "url": req.url,
                "instructions": req.instructions,
                "target_domain": req.target_domain,
                "spider_mode": req.spider_mode,
            },
        )
    except Exception as e:
        logger.error("crawl_enqueue_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"job_id": job_id, "status": "queued"}


@crawl_router.post("/{request_id}/cancel")
async def cancel_crawl(request_id: str, request: Request):
    """크롤링 작업을 취소한다."""
    await _authenticate_crawl(request)

    try:
        uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid request_id: {request_id}")

    state = request.app.state

    logger.info("crawl_cancel", request_id=request_id)

    try:
        await state.job_queue.enqueue(
            queue_name="crawl_cancel",
            payload={"request_id": request_id},
        )
    except Exception as e:
        logger.error("crawl_cancel_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    return {"status": "cancel_requested"}


@crawl_router.post("/generate-instructions")
async def generate_instructions(req: GenerateInstructionsRequest, request: Request):
    """사용자 설명으로부터 크롤링 작업지시서를 AI 생성한다."""
    await _authenticate_crawl(request)

    if not req.description.strip():
        raise HTTPException(status_code=400, detail="description is required")

    state = request.app.state

    logger.info("generate_instructions", desc_len=len(req.description))

    try:
        llm = state.llm_provider
        prompt = (
            "다음 사용자 설명을 기반으로 웹 크롤링 작업지시서를 생성하세요.\n"
            "작업지시서는 크롤링 대상 URL, 수집할 데이터 유형, 깊이 제한, "
            "제외 패턴 등을 포함해야 합니다.\n\n"
            f"사용자 설명:\n{req.description}\n\n"
            "작업지시서:"
        )
        result = await llm.generate(prompt, max_tokens=2048)
        instructions = result.strip()
    except Exception as e:
        logger.error("generate_instructions_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="작업지시서 생성에 실패했습니다")

    return {"instructions": instructions}
