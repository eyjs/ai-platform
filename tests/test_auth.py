"""인증 미들웨어 단위 테스트."""

import hashlib
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.gateway.auth import AuthError, AuthService, generate_api_key
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


# --- 개발 모드 (auth_required=False) ---


@pytest.mark.asyncio
async def test_auth_disabled_returns_anonymous(auth_service_disabled):
    """인증 비활성 시 anonymous VIEWER 반환."""
    ctx = await auth_service_disabled.authenticate()
    assert ctx.user_id == "dev-anonymous"
    assert ctx.user_role == "VIEWER"
    assert ctx.security_level_max == "PUBLIC"


# --- 인증 필수 모드 ---


@pytest.mark.asyncio
async def test_no_credentials_raises_error(auth_service):
    """인증 헤더 없으면 AuthError."""
    with pytest.raises(AuthError, match="인증이 필요합니다"):
        await auth_service.authenticate()


# --- JWT ---


@pytest.mark.asyncio
async def test_jwt_valid_token(auth_service):
    """유효한 JWT 토큰 → UserContext."""
    import jwt
    token = jwt.encode(
        {"sub": "user-123", "role": "EDITOR", "security_level_max": "INTERNAL"},
        "test-secret",
        algorithm="HS256",
    )
    ctx = await auth_service.authenticate(authorization=f"Bearer {token}")
    assert ctx.user_id == "user-123"
    assert ctx.user_role == "EDITOR"
    assert ctx.security_level_max == "INTERNAL"


@pytest.mark.asyncio
async def test_jwt_expired_token(auth_service):
    """만료된 JWT → AuthError."""
    import jwt
    import time
    token = jwt.encode(
        {"sub": "user-123", "exp": int(time.time()) - 100},
        "test-secret",
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="만료"):
        await auth_service.authenticate(authorization=f"Bearer {token}")


@pytest.mark.asyncio
async def test_jwt_wrong_secret(auth_service):
    """잘못된 시크릿 → AuthError."""
    import jwt
    token = jwt.encode({"sub": "user-123"}, "wrong-secret", algorithm="HS256")
    with pytest.raises(AuthError, match="유효하지 않은 토큰"):
        await auth_service.authenticate(authorization=f"Bearer {token}")


@pytest.mark.asyncio
async def test_jwt_invalid_role(auth_service):
    """유효하지 않은 역할 → AuthError."""
    import jwt
    token = jwt.encode(
        {"sub": "user-123", "role": "SUPERADMIN"},
        "test-secret",
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="유효하지 않은 역할"):
        await auth_service.authenticate(authorization=f"Bearer {token}")


# --- API Key ---


@pytest.mark.asyncio
async def test_api_key_valid(auth_service, mock_pool):
    """유효한 API Key → UserContext."""
    raw_key = "aip_test_key_123"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    mock_pool.fetchrow.return_value = {
        "user_id": "api-user-1",
        "user_role": "EDITOR",
        "security_level_max": "INTERNAL",
        "allowed_profiles": ["insurance-qa"],
        "rate_limit_per_min": 100,
        "expires_at": None,
    }

    ctx = await auth_service.authenticate(api_key=raw_key)
    assert ctx.user_id == "api-user-1"
    assert ctx.user_role == "EDITOR"
    assert ctx.allowed_profiles == ["insurance-qa"]

    # DB에서 올바른 해시로 조회했는지 확인
    call_args = mock_pool.fetchrow.call_args
    assert key_hash in str(call_args)


@pytest.mark.asyncio
async def test_api_key_not_found(auth_service, mock_pool):
    """존재하지 않는 API Key → AuthError."""
    mock_pool.fetchrow.return_value = None
    with pytest.raises(AuthError, match="유효하지 않은 API Key"):
        await auth_service.authenticate(api_key="invalid_key")


@pytest.mark.asyncio
async def test_api_key_expired(auth_service, mock_pool):
    """만료된 API Key → AuthError."""
    from datetime import datetime, timezone, timedelta
    mock_pool.fetchrow.return_value = {
        "user_id": "api-user",
        "user_role": "VIEWER",
        "security_level_max": "PUBLIC",
        "allowed_profiles": [],
        "rate_limit_per_min": 60,
        "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
    }
    with pytest.raises(AuthError, match="만료된 API Key"):
        await auth_service.authenticate(api_key="some_key")


# --- 프로필 접근 제어 ---


@pytest.mark.asyncio
async def test_profile_access_allowed(auth_service):
    """allowed_profiles에 포함된 프로필 → 통과."""
    ctx = UserContext(
        user_id="u1", user_role="VIEWER",
        allowed_profiles=["insurance-qa", "general-chat"],
    )
    await auth_service.check_profile_access(ctx, "insurance-qa")  # 예외 없으면 통과


@pytest.mark.asyncio
async def test_profile_access_denied(auth_service):
    """allowed_profiles에 없는 프로필 → AuthError."""
    ctx = UserContext(
        user_id="u1", user_role="VIEWER",
        allowed_profiles=["general-chat"],
    )
    with pytest.raises(AuthError, match="접근 권한이 없습니다"):
        await auth_service.check_profile_access(ctx, "insurance-qa")


@pytest.mark.asyncio
async def test_profile_access_empty_allows_all(auth_service):
    """allowed_profiles 비어있으면 모든 프로필 허용."""
    ctx = UserContext(user_id="u1", user_role="VIEWER", allowed_profiles=[])
    await auth_service.check_profile_access(ctx, "any-profile")


@pytest.mark.asyncio
async def test_profile_access_auth_disabled(auth_service_disabled):
    """인증 비활성이면 항상 통과."""
    ctx = UserContext(
        user_id="u1", user_role="VIEWER",
        allowed_profiles=["other-profile"],
    )
    await auth_service_disabled.check_profile_access(ctx, "insurance-qa")


# --- API Key 생성 유틸리티 ---


def test_generate_api_key():
    """API Key 생성 시 raw_key + hash 쌍 반환."""
    raw_key, key_hash = generate_api_key()
    assert raw_key.startswith("aip_")
    assert len(key_hash) == 64  # SHA-256 hex
    assert hashlib.sha256(raw_key.encode()).hexdigest() == key_hash


def test_generate_api_key_unique():
    """매번 다른 키가 생성됨."""
    keys = [generate_api_key()[0] for _ in range(10)]
    assert len(set(keys)) == 10
