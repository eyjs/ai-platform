"""인증 미들웨어: JWT + API Key 검증.

인증 흐름:
1. Authorization: Bearer <jwt> → JWT 디코딩 → UserContext
2. X-API-Key: <key> → DB 조회 (SHA-256 해시) → UserContext
3. 헤더 없음 → 401 Unauthorized

설정:
- AIP_AUTH_REQUIRED=false → 개발 모드 (모든 요청 anonymous VIEWER로 통과)
- AIP_JWT_SECRET → JWT 서명 검증 키
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import asyncpg

from src.domain.models import SecurityLevel, UserRole
from src.domain.key_type_policy import (
    SECRET,
    VALID_KEY_TYPES,
    clamp_security_for_publishable,
    validate_publishable_config,
)
from src.domain.profile_authz import is_profile_allowed, resolve_allowed_profiles
from src.gateway.models import UserContext
from src.observability.logging import get_logger

if TYPE_CHECKING:
    from src.gateway.access_policy import AccessPolicyStore

logger = get_logger(__name__)

# JWT는 선택적 의존성 (PyJWT)
try:
    import jwt as pyjwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False


class AuthService:
    """JWT + API Key 인증 서비스."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        jwt_secret: str = "",
        auth_required: bool = True,
        access_policy: AccessPolicyStore | None = None,
        profile_auth_strict: bool = False,
        publishable_rate_limit_max: int = 120,
        jwt_public_key: str = "",
        jwt_hs256_fallback: bool = True,
    ):
        self._pool = pool
        self._jwt_secret = jwt_secret
        self._auth_required = auth_required
        self._access_policy = access_policy
        self._profile_auth_strict = profile_auth_strict
        self._publishable_rate_limit_max = publishable_rate_limit_max
        # D17: RSA 공개키(PEM). 설정 시 RS256 검증 활성. api는 개인키를 갖지 않는다.
        self._jwt_public_key = jwt_public_key
        # D17 과도기: HS256(공유 시크릿) 허용. 전환 완료 후 false로 잠금.
        self._jwt_hs256_fallback = jwt_hs256_fallback
        self._background_tasks: set[asyncio.Task] = set()

    async def authenticate(
        self,
        authorization: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> UserContext:
        """요청 헤더에서 인증 정보를 추출한다.

        Returns:
            UserContext: 인증된 사용자 맥락

        Raises:
            AuthError: 인증 실패
        """
        # 개발 모드: 인증 비활성
        if not self._auth_required:
            return UserContext(
                user_id="dev-anonymous",
                user_role=UserRole.VIEWER,
                security_level_max=SecurityLevel.PUBLIC,
            )

        # 1. JWT Bearer 토큰
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
            return self._verify_jwt(token)

        # 2. API Key
        if api_key:
            return await self._verify_api_key(api_key)

        # 3. 인증 없음
        raise AuthError("인증이 필요합니다. Authorization 또는 X-API-Key 헤더를 제공하세요.")

    def _verify_jwt(self, token: str) -> UserContext:
        """JWT 토큰을 검증하고 UserContext를 반환한다 (D17 듀얼-모드).

        토큰 헤더의 alg로 검증 경로를 고정한다 — 키와 알고리즘을 교차시키지
        않아 알고리즘 혼동(algorithm confusion) 공격을 차단한다:
          - RS256: 공개키로만 검증 (개인키는 bff에만 존재)
          - HS256: 과도기 폴백이 켜진 경우에만 공유 시크릿으로 검증
        """
        if not HAS_JWT:
            raise AuthError("JWT 지원이 설치되지 않았습니다 (pip install PyJWT)")

        try:
            header = pyjwt.get_unverified_header(token)
        except pyjwt.InvalidTokenError as e:
            raise AuthError(f"유효하지 않은 토큰: {e}")

        alg = header.get("alg", "")
        if alg == "RS256":
            if not self._jwt_public_key:
                raise AuthError("RS256 토큰을 검증할 공개키가 설정되지 않았습니다")
            key: str = self._jwt_public_key
        elif alg == "HS256":
            if not self._jwt_hs256_fallback:
                raise AuthError("HS256 토큰은 더 이상 허용되지 않습니다 (RS256 전환 완료)")
            if not self._jwt_secret:
                raise AuthError("JWT 시크릿이 설정되지 않았습니다")
            key = self._jwt_secret
        else:
            raise AuthError(f"지원하지 않는 JWT 알고리즘: {alg or '없음'}")

        try:
            payload = pyjwt.decode(token, key, algorithms=[alg])
        except pyjwt.ExpiredSignatureError:
            raise AuthError("토큰이 만료되었습니다")
        except pyjwt.InvalidTokenError as e:
            raise AuthError(f"유효하지 않은 토큰: {e}")

        user_id = payload.get("sub", "")
        user_role = payload.get("role", UserRole.VIEWER)
        security_max = payload.get("security_level_max", SecurityLevel.PUBLIC)
        user_type = payload.get("user_type", "")

        if user_role not in UserRole.__members__.values():
            raise AuthError(f"유효하지 않은 역할: {user_role}")

        # A1+D17: 프로필 인가 해석.
        # JWT는 RS256로 서명된 신뢰 자격이다. 선택적 allowed_profiles 클레임을
        # 존중하되, 클레임이 없으면:
        #   - ADMIN: 전체 허용(["*"]) — 운영자는 bff로 이미 전 프로필을 관리한다.
        #   - 그 외: 빈 목록 → strict 정책(deny-by-default)을 그대로 적용.
        allowed_profiles = payload.get("allowed_profiles")
        if allowed_profiles is None:
            allowed_profiles = ["*"] if user_role == UserRole.ADMIN else []

        return UserContext(
            user_id=user_id,
            user_role=user_role,
            security_level_max=security_max,
            user_type=user_type,
            allowed_profiles=allowed_profiles,
        )

    async def _verify_api_key(self, raw_key: str) -> UserContext:
        """API Key를 DB에서 검증한다."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        row = await self._pool.fetchrow(
            """
            SELECT id, user_id, user_role, security_level_max, user_type,
                   allowed_profiles, allowed_origins, rate_limit_per_min,
                   expires_at, tenant_id, key_type
            FROM api_keys
            WHERE key_hash = $1 AND is_active = TRUE
            """,
            key_hash,
        )

        if not row:
            raise AuthError("유효하지 않은 API Key")

        # 만료 확인
        if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc):
            raise AuthError("만료된 API Key")

        # last_used_at 갱신 — 메인 요청 흐름을 막지 않도록 백그라운드 태스크로 분리
        task = asyncio.create_task(self._update_last_used(key_hash))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        # key_type 결정 (레거시/마이그레이션 전 행은 None → secret 취급)
        key_type = row.get("key_type") or SECRET
        security_level_max = row["security_level_max"]
        # publishable 키는 런타임에 보안등급을 PUBLIC로 클램프 (defense-in-depth)
        if key_type != SECRET:
            security_level_max = clamp_security_for_publishable(security_level_max)

        return UserContext(
            user_id=row["user_id"] or "api-user",
            user_role=row["user_role"],
            security_level_max=security_level_max,
            user_type=row["user_type"] or "",
            allowed_profiles=row["allowed_profiles"] or [],
            allowed_origins=row["allowed_origins"] or [],
            rate_limit_per_min=row["rate_limit_per_min"] or 60,
            tenant_id=row["tenant_id"],
            key_type=key_type,
            api_key_id=str(row["id"]) if row.get("id") is not None else None,
        )

    async def check_profile_access(
        self,
        user_ctx: UserContext,
        chatbot_id: str,
    ) -> None:
        """사용자가 해당 프로필에 접근 권한이 있는지 확인한다.

        A1: profile_auth_strict=True면 빈 allowed_profiles = 전체 거부(deny-by-default).
        명시적 전체 허용은 와일드카드 "*"로. strict=False면 빈 목록=전체 허용(레거시).
        """
        if not self._auth_required:
            return

        allowed = resolve_allowed_profiles(
            user_ctx.allowed_profiles, strict=self._profile_auth_strict
        )
        if not is_profile_allowed(allowed, chatbot_id):
            raise AuthError(
                f"'{chatbot_id}' 프로필에 접근 권한이 없습니다"
            )

        # segment 교차 검증: AccessPolicyStore가 주입된 경우에만 수행
        if self._access_policy and not self._access_policy.is_allowed(chatbot_id, user_ctx.user_type):
            raise AuthError(
                f"사용자군 '{user_ctx.user_type}'은(는) '{chatbot_id}' 프로필에 접근 권한이 없습니다"
            )

    def check_origin(
        self,
        user_ctx: UserContext,
        origin: str | None,
    ) -> None:
        """요청 Origin이 허용된 도메인인지 확인한다."""
        if not self._auth_required:
            return

        if not user_ctx.allowed_origins:
            return  # 제한 없음

        if not origin:
            raise AuthError("Origin 헤더가 필요합니다")

        if origin not in user_ctx.allowed_origins:
            raise AuthError(
                f"허용되지 않은 Origin: {origin}"
            )

    async def create_key(
        self,
        name: str,
        creator_user_id: str,
        user_role: str = "VIEWER",
        security_level_max: str = "PUBLIC",
        allowed_profiles: list[str] | None = None,
        allowed_origins: list[str] | None = None,
        rate_limit_per_min: int = 60,
        user_type: str = "",
        key_type: str = SECRET,
    ) -> tuple[str, str]:
        """새 API Key를 생성하고 DB에 저장한다.

        Returns:
            (raw_key, key_hash): raw_key는 한 번만 노출

        Raises:
            AuthError: 유효하지 않은 role/security_level
        """
        valid_roles = set(UserRole.__members__.values())
        if user_role not in valid_roles:
            raise AuthError(f"유효하지 않은 역할: {user_role} (허용: {valid_roles})")

        valid_levels = set(SecurityLevel.__members__.values())
        if security_level_max not in valid_levels:
            raise AuthError(f"유효하지 않은 보안등급: {security_level_max} (허용: {valid_levels})")

        if key_type not in VALID_KEY_TYPES:
            raise AuthError(f"유효하지 않은 키 종류: {key_type} (허용: {set(VALID_KEY_TYPES)})")

        # publishable 키는 발급 시점에 강한 제약을 강제 (오리진·보안등급·역할·쿼터)
        if key_type != SECRET:
            violation = validate_publishable_config(
                security_level_max=security_level_max,
                user_role=user_role,
                allowed_origins=allowed_origins,
                rate_limit_per_min=rate_limit_per_min,
                rate_limit_cap=self._publishable_rate_limit_max,
            )
            if violation:
                raise AuthError(violation)

        raw_key, key_hash = generate_api_key()

        await self._pool.execute(
            """
            INSERT INTO api_keys (key_hash, name, user_id, user_role, security_level_max,
                                  allowed_profiles, allowed_origins, rate_limit_per_min,
                                  user_type, key_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            key_hash, name, creator_user_id, user_role, security_level_max,
            allowed_profiles or [], allowed_origins or [], rate_limit_per_min,
            user_type, key_type,
        )

        logger.info(
            "api_key_created",
            name=name,
            user_role=user_role,
            key_type=key_type,
            origins=allowed_origins or [],
        )
        return raw_key, key_hash

    async def _update_last_used(self, key_hash: str) -> None:
        """API Key 마지막 사용 시각을 백그라운드로 갱신한다."""
        try:
            await self._pool.execute(
                "UPDATE api_keys SET last_used_at = NOW() WHERE key_hash = $1",
                key_hash,
            )
        except Exception as e:
            logger.error("api_key_last_used_update_failed", key_hash=key_hash[:8], error=str(e))


class AuthError(Exception):
    """인증 오류."""

    pass


# --- API Key 생성 유틸리티 ---


def generate_api_key() -> tuple[str, str]:
    """새 API Key를 생성하고 (raw_key, key_hash)를 반환한다.

    raw_key는 사용자에게 한 번만 보여주고, key_hash만 DB에 저장.
    """
    raw_key = f"aip_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash
