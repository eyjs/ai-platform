"""Fortune 해석 API Router.

POST /api/fortune/interpret — 동기 운세 해석 (단일 LLM 호출)
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.gateway.auth import AuthService
from src.gateway.models import UserContext
from src.observability.logging import get_logger
from src.services.response_cache_models import normalize_input

logger = get_logger(__name__)

fortune_router = APIRouter()

# 무료 콘텐츠 캐시 — 같은 입력은 재생성 없이 즉답($0·GPU 절약). 타입별 TTL.
_CACHE_PROFILE = "saju-fortune"
_CACHE_MODE = "deterministic"
_FORTUNE_TTL = {
    "today": 86400, "yearly": 2592000, "tojeong": 604800,
    "tarot": 86400, "dream": 86400, "name": 86400, "charm": 86400, "compare": 604800,
}


class FortuneInterpretRequest(BaseModel):
    """운세 해석 요청."""

    type: str = Field(..., description="운세/놀이 타입", pattern=r"^(today|yearly|tojeong|tarot|dream|name|charm|compare)$")
    saju_context: str = Field(..., description="컨텍스트 (사전 포맷팅 완료)", min_length=10)
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
        cache = getattr(request.app.state, "response_cache_service", None)

        # 캐시 키: 타입+컨텍스트(+괘). 같은 입력이면 로컬 LLM 재호출 없이 즉답.
        normalized = normalize_input(
            f"{request_data.type}|{request_data.saju_context}|{request_data.tojeong_data or ''}"
        )

        if cache is not None:
            hit = await cache.get(_CACHE_PROFILE, _CACHE_MODE, normalized)
            if hit is not None:
                logger.info("fortune_cache_hit", fortune_type=request_data.type)
                return FortuneInterpretResponse(fortune_data=json.loads(hit.response_text))

        fortune_data = await fortune_service.interpret(
            fortune_type=request_data.type,
            saju_context=request_data.saju_context,
            tojeong_data=request_data.tojeong_data,
        )

        if cache is not None:
            try:
                await cache.put(
                    _CACHE_PROFILE, _CACHE_MODE, normalized,
                    json.dumps(fortune_data, ensure_ascii=False),
                    ttl_seconds=_FORTUNE_TTL.get(request_data.type, 86400),
                )
            except Exception as ce:  # 캐시 실패는 비차단
                logger.warning("fortune_cache_put_failed error=%s", ce)

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
