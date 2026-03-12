"""인증 모듈: JWT / API Key 검증."""

import logging
from typing import Optional

from fastapi import HTTPException, Request

from src.gateway.models import UserContext

logger = logging.getLogger(__name__)


async def authenticate(request: Request) -> UserContext:
    """요청에서 인증 정보를 추출한다.

    MVP: API Key 기반 간단 인증.
    향후: JWT + API Key + UserContext 암호화.
    """
    api_key = request.headers.get("X-API-Key")
    chatbot_id = request.headers.get("X-Chatbot-Id")

    # MVP: 인증 없이 통과 (개발 모드)
    if not api_key:
        return UserContext(
            user_id="anonymous",
            user_role="VIEWER",
            security_level_max="PUBLIC",
        )

    # 향후: DB에서 API Key 검증 + allowed_profiles 확인
    return UserContext(
        user_id="api-user",
        user_role="VIEWER",
        security_level_max="PUBLIC",
    )
