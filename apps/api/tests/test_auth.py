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
async def test_jwt_admin_gets_wildcard_profiles(auth_service):
    """A1+D17: 클레임 없는 ADMIN JWT는 전체 프로필 허용(['*'])."""
    import jwt
    token = jwt.encode(
        {"sub": "admin-1", "role": "ADMIN"}, "test-secret", algorithm="HS256",
    )
    ctx = await auth_service.authenticate(authorization=f"Bearer {token}")
    assert ctx.allowed_profiles == ["*"]


@pytest.mark.asyncio
async def test_jwt_non_admin_no_claim_empty_profiles(auth_service):
    """클레임 없는 비-ADMIN JWT는 빈 목록 → strict 정책이 적용된다."""
    import jwt
    token = jwt.encode(
        {"sub": "u-1", "role": "VIEWER"}, "test-secret", algorithm="HS256",
    )
    ctx = await auth_service.authenticate(authorization=f"Bearer {token}")
    assert ctx.allowed_profiles == []


@pytest.mark.asyncio
async def test_jwt_honors_allowed_profiles_claim(auth_service):
    """allowed_profiles 클레임이 있으면 그대로 존중한다 (ADMIN이라도)."""
    import jwt
    token = jwt.encode(
        {"sub": "u-2", "role": "ADMIN", "allowed_profiles": ["insurance-qa"]},
        "test-secret",
        algorithm="HS256",
    )
    ctx = await auth_service.authenticate(authorization=f"Bearer {token}")
    assert ctx.allowed_profiles == ["insurance-qa"]


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
        "user_type": "",
        "allowed_profiles": ["insurance-qa"],
        "allowed_origins": ["https://customer.com"],
        "rate_limit_per_min": 100,
        "expires_at": None,
        "tenant_id": None,
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
        "user_type": "",
        "allowed_profiles": [],
        "allowed_origins": [],
        "rate_limit_per_min": 60,
        "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
        "tenant_id": None,
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
    """[레거시 fail-open] strict=False면 allowed_profiles 비어있을 때 모든 프로필 허용."""
    ctx = UserContext(user_id="u1", user_role="VIEWER", allowed_profiles=[])
    await auth_service.check_profile_access(ctx, "any-profile")


# --- A1: deny-by-default (profile_auth_strict=True) ---


@pytest.fixture
def auth_service_strict(mock_pool):
    return AuthService(
        pool=mock_pool, jwt_secret="test-secret",
        auth_required=True, profile_auth_strict=True,
    )


@pytest.mark.asyncio
async def test_profile_access_strict_empty_denies_all(auth_service_strict):
    """[네거티브] strict면 빈 allowed_profiles = 전체 거부."""
    ctx = UserContext(user_id="u1", user_role="VIEWER", allowed_profiles=[])
    with pytest.raises(AuthError):
        await auth_service_strict.check_profile_access(ctx, "any-profile")


@pytest.mark.asyncio
async def test_profile_access_strict_not_in_list_denies(auth_service_strict):
    """[네거티브] strict면 허용목록 밖 프로필 거부."""
    ctx = UserContext(user_id="u1", user_role="VIEWER", allowed_profiles=["general-chat"])
    with pytest.raises(AuthError):
        await auth_service_strict.check_profile_access(ctx, "insurance-qa")


@pytest.mark.asyncio
async def test_profile_access_strict_in_list_passes(auth_service_strict):
    """strict여도 명시 허용된 프로필은 통과."""
    ctx = UserContext(user_id="u1", user_role="VIEWER", allowed_profiles=["insurance-qa"])
    await auth_service_strict.check_profile_access(ctx, "insurance-qa")


@pytest.mark.asyncio
async def test_profile_access_strict_wildcard_allows_all(auth_service_strict):
    """strict여도 와일드카드 "*"는 명시적 전체 허용."""
    ctx = UserContext(user_id="u1", user_role="VIEWER", allowed_profiles=["*"])
    await auth_service_strict.check_profile_access(ctx, "any-profile")


@pytest.mark.asyncio
async def test_profile_access_auth_disabled(auth_service_disabled):
    """인증 비활성이면 항상 통과."""
    ctx = UserContext(
        user_id="u1", user_role="VIEWER",
        allowed_profiles=["other-profile"],
    )
    await auth_service_disabled.check_profile_access(ctx, "insurance-qa")


# --- Origin 도메인 제한 ---


def test_origin_allowed(auth_service):
    """허용된 Origin → 통과."""
    ctx = UserContext(
        user_id="u1", user_role="VIEWER",
        allowed_origins=["https://customer.com", "https://app.customer.com"],
    )
    auth_service.check_origin(ctx, "https://customer.com")


def test_origin_denied(auth_service):
    """허용되지 않은 Origin → AuthError."""
    ctx = UserContext(
        user_id="u1", user_role="VIEWER",
        allowed_origins=["https://customer.com"],
    )
    with pytest.raises(AuthError, match="허용되지 않은 Origin"):
        auth_service.check_origin(ctx, "https://hacker-site.com")


def test_origin_missing_header(auth_service):
    """allowed_origins 설정됐는데 Origin 헤더 없음 → AuthError."""
    ctx = UserContext(
        user_id="u1", user_role="VIEWER",
        allowed_origins=["https://customer.com"],
    )
    with pytest.raises(AuthError, match="Origin 헤더가 필요합니다"):
        auth_service.check_origin(ctx, None)


def test_origin_empty_allows_all(auth_service):
    """allowed_origins 비어있으면 제한 없음."""
    ctx = UserContext(user_id="u1", user_role="VIEWER", allowed_origins=[])
    auth_service.check_origin(ctx, "https://any-site.com")


def test_origin_auth_disabled(auth_service_disabled):
    """인증 비활성이면 Origin 검사 스킵."""
    ctx = UserContext(
        user_id="u1", user_role="VIEWER",
        allowed_origins=["https://customer.com"],
    )
    auth_service_disabled.check_origin(ctx, "https://hacker-site.com")


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


# --- AuthService.create_key 입력 검증 ---


@pytest.mark.asyncio
async def test_create_key_invalid_role(auth_service, mock_pool):
    """유효하지 않은 역할로 키 생성 시 AuthError."""
    with pytest.raises(AuthError, match="유효하지 않은 역할"):
        await auth_service.create_key(
            name="test", creator_user_id="u1", user_role="SUPERADMIN",
        )
    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_create_key_invalid_security_level(auth_service, mock_pool):
    """유효하지 않은 보안등급으로 키 생성 시 AuthError."""
    with pytest.raises(AuthError, match="유효하지 않은 보안등급"):
        await auth_service.create_key(
            name="test", creator_user_id="u1", security_level_max="TOP_SECRET",
        )
    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_create_key_valid(auth_service, mock_pool):
    """정상 키 생성 → DB INSERT 호출 + raw_key/hash 반환."""
    raw_key, key_hash = await auth_service.create_key(
        name="widget-key",
        creator_user_id="admin-1",
        user_role="VIEWER",
        allowed_origins=["https://customer.com"],
    )
    assert raw_key.startswith("aip_")
    assert len(key_hash) == 64
    assert mock_pool.execute.called


# --- publishable/secret 키 분리 (B4) ---


@pytest.mark.asyncio
async def test_create_publishable_requires_origins(auth_service, mock_pool):
    """publishable 키는 오리진 없이 발급 불가 → AuthError, INSERT 미호출."""
    with pytest.raises(AuthError, match="오리진"):
        await auth_service.create_key(
            name="widget", creator_user_id="admin-1", user_role="VIEWER",
            security_level_max="PUBLIC", allowed_origins=[], key_type="publishable",
        )
    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_create_publishable_rejects_internal(auth_service, mock_pool):
    """publishable 키는 PUBLIC 초과 보안등급 거부."""
    with pytest.raises(AuthError, match="보안등급"):
        await auth_service.create_key(
            name="widget", creator_user_id="admin-1", user_role="VIEWER",
            security_level_max="INTERNAL", allowed_origins=["https://shop.com"],
            key_type="publishable",
        )
    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_create_publishable_rejects_write_role(auth_service, mock_pool):
    """publishable 키는 VIEWER 외 역할 거부."""
    with pytest.raises(AuthError, match="VIEWER"):
        await auth_service.create_key(
            name="widget", creator_user_id="admin-1", user_role="EDITOR",
            security_level_max="PUBLIC", allowed_origins=["https://shop.com"],
            key_type="publishable",
        )
    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_create_publishable_rate_cap(auth_service, mock_pool):
    """publishable 키는 쿼터 상한 초과 거부 (기본 120)."""
    with pytest.raises(AuthError, match="쿼터"):
        await auth_service.create_key(
            name="widget", creator_user_id="admin-1", user_role="VIEWER",
            security_level_max="PUBLIC", allowed_origins=["https://shop.com"],
            rate_limit_per_min=999, key_type="publishable",
        )
    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_create_publishable_valid(auth_service, mock_pool):
    """제약을 만족하는 publishable 키 → 발급 성공, INSERT 호출."""
    raw_key, key_hash = await auth_service.create_key(
        name="widget", creator_user_id="admin-1", user_role="VIEWER",
        security_level_max="PUBLIC", allowed_origins=["https://shop.com"],
        rate_limit_per_min=60, key_type="publishable",
    )
    assert raw_key.startswith("aip_")
    assert mock_pool.execute.called
    # INSERT VALUES 마지막 인자에 key_type 전달됐는지
    assert "publishable" in str(mock_pool.execute.call_args)


@pytest.mark.asyncio
async def test_create_secret_unrestricted(auth_service, mock_pool):
    """secret 키(기본)는 INTERNAL·EDITOR·오리진 없음이어도 발급 성공(기존 권한 유지)."""
    raw_key, _ = await auth_service.create_key(
        name="server", creator_user_id="admin-1", user_role="EDITOR",
        security_level_max="INTERNAL", allowed_origins=[],
    )
    assert raw_key.startswith("aip_")
    assert mock_pool.execute.called


@pytest.mark.asyncio
async def test_create_key_invalid_key_type(auth_service, mock_pool):
    """알 수 없는 key_type → AuthError, INSERT 미호출."""
    with pytest.raises(AuthError, match="키 종류"):
        await auth_service.create_key(
            name="x", creator_user_id="admin-1", user_role="VIEWER",
            allowed_origins=["https://shop.com"], key_type="bogus",
        )
    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_verify_publishable_clamps_security(auth_service, mock_pool):
    """publishable 키 행이 INTERNAL이어도 검증 시 PUBLIC로 클램프(defense-in-depth)."""
    mock_pool.fetchrow.return_value = {
        "user_id": "widget-user",
        "user_role": "VIEWER",
        "security_level_max": "INTERNAL",
        "user_type": "",
        "allowed_profiles": ["shop-qa"],
        "allowed_origins": ["https://shop.com"],
        "rate_limit_per_min": 60,
        "expires_at": None,
        "tenant_id": None,
        "key_type": "publishable",
    }
    ctx = await auth_service.authenticate(api_key="aip_widget")
    assert ctx.key_type == "publishable"
    assert ctx.security_level_max == "PUBLIC"


@pytest.mark.asyncio
async def test_verify_secret_keeps_security(auth_service, mock_pool):
    """secret 키는 INTERNAL 보안등급 그대로 유지 (클램프 없음)."""
    mock_pool.fetchrow.return_value = {
        "user_id": "server-user",
        "user_role": "EDITOR",
        "security_level_max": "INTERNAL",
        "user_type": "",
        "allowed_profiles": [],
        "allowed_origins": [],
        "rate_limit_per_min": 60,
        "expires_at": None,
        "tenant_id": None,
        "key_type": "secret",
    }
    ctx = await auth_service.authenticate(api_key="aip_server")
    assert ctx.key_type == "secret"
    assert ctx.security_level_max == "INTERNAL"


@pytest.mark.asyncio
async def test_verify_legacy_row_defaults_secret(auth_service, mock_pool):
    """key_type 컬럼이 없는 레거시 행(마이그레이션 전) → secret 취급, 클램프 없음."""
    mock_pool.fetchrow.return_value = {
        "user_id": "legacy-user",
        "user_role": "EDITOR",
        "security_level_max": "INTERNAL",
        "user_type": "",
        "allowed_profiles": [],
        "allowed_origins": [],
        "rate_limit_per_min": 60,
        "expires_at": None,
        "tenant_id": None,
        # key_type 키 없음 → row.get() None → secret
    }
    ctx = await auth_service.authenticate(api_key="aip_legacy")
    assert ctx.key_type == "secret"
    assert ctx.security_level_max == "INTERNAL"
