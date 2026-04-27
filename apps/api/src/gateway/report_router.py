"""Saju Report API Router.

HTTP 엔드포인트를 통한 사주 리포트 생성/조회:
- POST /api/report/generate — 리포트 생성 요청 (Job Queue에 추가)
- GET /api/report/status/{job_id} — 진행 상태 조회
- GET /api/report/result/{job_id} — 완성된 리포트 데이터 조회
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.gateway.auth import AuthService
from src.gateway.models import UserContext
from src.infrastructure.job_queue import JobQueue
from src.observability.logging import get_logger

logger = get_logger(__name__)

report_router = APIRouter()


class ReportGenerateRequest(BaseModel):
    """리포트 생성 요청."""

    report_type: str = Field(..., description="리포트 타입", regex=r"^(paper|compatibility)$")
    saju_data: Dict[str, Any] = Field(..., description="사주 데이터")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="추가 메타데이터")


class ReportGenerateResponse(BaseModel):
    """리포트 생성 응답."""

    job_id: str = Field(..., description="작업 ID")
    status: str = Field(default="queued", description="초기 상태")


class ReportStatusResponse(BaseModel):
    """리포트 상태 응답."""

    job_id: str
    status: str = Field(..., description="상태: generating, completed, failed")
    sections_completed: int = Field(0, description="완료된 섹션 수")
    sections_total: int = Field(0, description="전체 섹션 수")
    error_message: Optional[str] = Field(None, description="에러 메시지 (failed일 때)")
    created_at: Optional[str] = Field(None, description="생성 시간")
    completed_at: Optional[str] = Field(None, description="완료 시간")


class ReportResultResponse(BaseModel):
    """리포트 결과 응답."""

    job_id: str
    status: str
    report_type: str
    report_data: Dict[str, Any] = Field(..., description="완성된 리포트 데이터")


def get_auth_service(request: Request) -> AuthService:
    """AuthService 의존성 주입."""
    return request.app.state.auth_service


def get_job_queue(request: Request) -> JobQueue:
    """JobQueue 의존성 주입."""
    return request.app.state.job_queue


async def get_user_context(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> UserContext:
    """JWT/API Key로 인증된 사용자 맥락을 추출."""
    authorization = request.headers.get("Authorization")
    api_key = request.headers.get("X-API-Key")
    return await auth_service.authenticate(authorization=authorization, api_key=api_key)


@report_router.post("/report/generate", response_model=ReportGenerateResponse, status_code=202)
async def generate_report(
    request_data: ReportGenerateRequest,
    user_context: UserContext = Depends(get_user_context),
    job_queue: JobQueue = Depends(get_job_queue),
) -> ReportGenerateResponse:
    """사주 리포트 생성 요청.

    Job Queue에 작업을 추가하고 202 Accepted + job_id를 반환한다.
    """
    try:
        # 작업 페이로드 구성
        payload = {
            "report_type": request_data.report_type,
            "saju_data": request_data.saju_data,
            "metadata": request_data.metadata,
            "user_id": user_context.user_id,
            "user_role": user_context.user_role.value,
        }

        # JobQueue에 작업 추가
        job_id = await job_queue.enqueue(
            queue_name="saju-report",
            payload=payload,
            priority=1,
            max_attempts=2,
        )

        logger.info(
            "report_job_enqueued",
            job_id=job_id,
            report_type=request_data.report_type,
            user_id=user_context.user_id,
        )

        return ReportGenerateResponse(job_id=job_id)

    except Exception as e:
        logger.error("report_generate_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="리포트 생성 요청 중 오류가 발생했습니다.")


@report_router.get("/report/status/{job_id}", response_model=ReportStatusResponse)
async def get_report_status(
    job_id: str,
    request: Request,
    user_context: UserContext = Depends(get_user_context),
) -> ReportStatusResponse:
    """리포트 생성 상태 조회."""
    try:
        # job_id UUID 검증
        try:
            uuid.UUID(job_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="올바르지 않은 job_id 형식입니다.")

        # SajuReportService를 통해 상태 조회
        saju_report_service = request.app.state.saju_report_service
        status_data = await saju_report_service.get_report_status(job_id)

        if not status_data:
            raise HTTPException(status_code=404, detail="해당 job_id를 찾을 수 없습니다.")

        return ReportStatusResponse(**status_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("report_status_error", job_id=job_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="상태 조회 중 오류가 발생했습니다.")


@report_router.get("/report/result/{job_id}", response_model=ReportResultResponse)
async def get_report_result(
    job_id: str,
    request: Request,
    user_context: UserContext = Depends(get_user_context),
) -> ReportResultResponse:
    """완성된 리포트 데이터 조회."""
    try:
        # job_id UUID 검증
        try:
            uuid.UUID(job_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="올바르지 않은 job_id 형식입니다.")

        # SajuReportService를 통해 결과 조회
        saju_report_service = request.app.state.saju_report_service
        result_data = await saju_report_service.get_report_result(job_id)

        if not result_data:
            raise HTTPException(status_code=404, detail="해당 job_id를 찾을 수 없습니다.")

        if result_data["status"] != "completed":
            detail = "리포트가 아직 완료되지 않았습니다."
            if result_data["status"] == "failed":
                detail = f"리포트 생성이 실패했습니다: {result_data.get('error_message', '')}"
            raise HTTPException(status_code=409, detail=detail)

        return ReportResultResponse(**result_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("report_result_error", job_id=job_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="결과 조회 중 오류가 발생했습니다.")