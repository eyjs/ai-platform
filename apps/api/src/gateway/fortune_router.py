"""Fortune 해석 API Router.

POST /api/fortune/interpret — 동기 운세 해석 (단일 LLM 호출)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.gateway.auth import AuthService
from src.gateway.models import UserContext
from src.observability.logging import get_logger

logger = get_logger(__name__)

fortune_router = APIRouter()


class FortuneInterpretRequest(BaseModel):
    """운세 해석 요청."""

    type: str = Field(..., description="운세 타입", pattern=r"^(today|yearly|tojeong)$")
    saju_context: str = Field(..., description="사주 컨텍스트 (사전 포맷팅 완료)", min_length=10)
    tojeong_data: Optional[Dict[str, Any]] = Field(None, description="토정비결 괘 데이터 (tojeong 타입일 때)")


class FortuneInterpretResponse(BaseModel):
    """운세 해석 응답."""

    status: str = Field(default="success")
    fortune_data: Dict[str, Any] = Field(..., description="해석 결과 JSON")


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


async def get_user_context(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> UserContext:
    authorization = request.headers.get("Authorization")
    api_key = request.headers.get("X-API-Key")
    return await auth_service.authenticate(authorization=authorization, api_key=api_key)


@fortune_router.post("/fortune/interpret", response_model=FortuneInterpretResponse)
async def interpret_fortune(
    request_data: FortuneInterpretRequest,
    request: Request,
    user_context: UserContext = Depends(get_user_context),
) -> FortuneInterpretResponse:
    """사주 운세 해석.

    saju-backend에서 사전 포맷팅된 saju_context를 받아
    LLM으로 운세를 해석하고 구조화된 JSON을 반환한다.
    """
    try:
        fortune_service = request.app.state.fortune_service

        fortune_data = await fortune_service.interpret(
            fortune_type=request_data.type,
            saju_context=request_data.saju_context,
            tojeong_data=request_data.tojeong_data,
        )

        logger.info(
            "fortune_interpret_success",
            fortune_type=request_data.type,
            user_id=user_context.user_id,
        )

        return FortuneInterpretResponse(fortune_data=fortune_data)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("fortune_interpret_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="운세 해석 중 오류가 발생했습니다.")
