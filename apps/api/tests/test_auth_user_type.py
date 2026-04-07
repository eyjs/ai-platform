"""user_type 필드 전파 테스트.

UserContext.user_type이 JWT, API Key, 개발 모드, create_key에서
올바르게 추출/전달되는지 검증한다.
"""

import hashlib
import pytest
from unittest.mock import AsyncMock

from src.gateway.auth import AuthService
from src.gateway.models import UserContext


@pytest.fixture
def mock_pool():
    return AsyncMock()


@pytest.fixture
def auth_service(mock_pool):
    return AuthService(pool=mock_pool, jwt_secret="test-secret", auth_required=True)


@pytest.fixture
def auth_service_disabled(mock_pool):
    return AuthService(pool=mock_pool, jwt_secret="", auth_required=False)


# --- JWT user_type ---


@pytest.mark.asyncio
async def test_jwt_extracts_user_type(auth_service):
    """JWT payload에 user_type이 있으면 UserContext에 전달된다."""
    import jwt

    token = jwt.encode(
        {"sub": "user-1", "role": "VIEWER", "user_type": "enterprise"},
        "test-secret",
        algorithm="HS256",
    )
    ctx = await auth_service.authenticate(authorization=f"Bearer {token}")
    assert ctx.user_type == "enterprise"


@pytest.mark.asyncio
async def test_jwt_missing_user_type_defaults_empty(auth_service):
    """JWT payload에 user_type이 없으면 빈 문자열 기본값."""
    import jwt

    token = jwt.encode(
        {"sub": "user-2", "role": "EDITOR"},
        "test-secret",
        algorithm="HS256",
    )
    ctx = await auth_service.authenticate(authorization=f"Bearer {token}")
    assert ctx.user_type == ""


# --- API Key user_type ---


@pytest.mark.asyncio
async def test_api_key_extracts_user_type(auth_service, mock_pool):
    """API Key DB row에 user_type이 있으면 UserContext에 전달된다."""
    raw_key = "aip_test_key_ut"

    mock_pool.fetchrow.return_value = {
        "user_id": "api-user-ut",
        "user_role": "VIEWER",
        "security_level_max": "PUBLIC",
        "user_type": "partner",
        "allowed_profiles": [],
        "allowed_origins": [],
        "rate_limit_per_min": 60,
        "expires_at": None,
        "tenant_id": None,
    }

    ctx = await auth_service.authenticate(api_key=raw_key)
    assert ctx.user_type == "partner"


# --- 개발 모드 ---


@pytest.mark.asyncio
async def test_dev_mode_user_type_empty(auth_service_disabled):
    """개발 모드(auth_required=False) 시 user_type은 빈 문자열."""
    ctx = await auth_service_disabled.authenticate()
    assert ctx.user_type == ""


# --- create_key with user_type ---


@pytest.mark.asyncio
async def test_create_key_with_user_type(auth_service, mock_pool):
    """create_key에 user_type을 전달하면 INSERT 쿼리에 포함된다."""
    raw_key, key_hash = await auth_service.create_key(
        name="typed-key",
        creator_user_id="admin-1",
        user_role="VIEWER",
        user_type="internal",
    )

    assert raw_key.startswith("aip_")
    assert len(key_hash) == 64

    # INSERT 호출 확인
    assert mock_pool.execute.called
    call_args = mock_pool.execute.call_args
    # SQL 문(첫 번째 인자) 제외, 바인딩 파라미터만 검사
    bind_params = call_args[0][1:]  # SQL 이후 positional args
    assert "internal" in bind_params
