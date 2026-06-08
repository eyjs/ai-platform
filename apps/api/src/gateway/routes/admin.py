"""API Key 관리 엔드포인트: /api-keys (ADMIN 전용)."""

from fastapi import APIRouter, HTTPException, Request

from src.domain.models import UserRole
from src.gateway.auth import AuthError
from src.gateway.routes.helpers import _authenticate, _get_app_state

router = APIRouter()


@router.post("/api-keys")
async def create_api_key(request: Request):
    """새 API Key를 생성한다. ADMIN만 가능."""
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    if user_ctx.user_role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="ADMIN 권한이 필요합니다")

    body = await request.json()
    name = body.get("name", "unnamed")
    user_role = body.get("user_role", UserRole.VIEWER)
    security_level_max = body.get("security_level_max", "PUBLIC")
    allowed_profiles = body.get("allowed_profiles", [])
    allowed_origins = body.get("allowed_origins", [])
    rate_limit = body.get("rate_limit_per_min", 60)
    user_type = body.get("user_type", "")
    key_type = body.get("key_type", "secret")

    try:
        raw_key, key_hash = await state.auth_service.create_key(
            name=name,
            creator_user_id=user_ctx.user_id,
            user_role=user_role,
            security_level_max=security_level_max,
            allowed_profiles=allowed_profiles,
            allowed_origins=allowed_origins,
            rate_limit_per_min=rate_limit,
            user_type=user_type,
            key_type=key_type,
        )
    except AuthError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "api_key": raw_key,
        "name": name,
        "key_type": key_type,
        "user_role": user_role,
        "security_level_max": security_level_max,
        "allowed_origins": allowed_origins,
        "message": "이 키는 다시 표시되지 않습니다. 안전하게 보관하세요.",
    }
