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
    ):
        self._pool = pool
        self._jwt_secret = jwt_secret
        self._auth_required = auth_required
        self._access_policy = access_policy
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
        """JWT 토큰을 검증하고 UserContext를 반환한다."""
        if not HAS_JWT:
            raise AuthError("JWT 지원이 설치되지 않았습니다 (pip install PyJWT)")

        if not self._jwt_secret:
            raise AuthError("JWT 시크릿이 설정되지 않았습니다")

        try:
            payload = pyjwt.decode(token, self._jwt_secret, algorithms=["HS256"])
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

        return UserContext(
            user_id=user_id,
            user_role=user_role,
            security_level_max=security_max,
            user_type=user_type,
        )

    async def _verify_api_key(self, raw_key: str) -> UserContext:
        """API Key를 DB에서 검증한다."""
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        row = await self._pool.fetchrow(
            """
            SELECT user_id, user_role, security_level_max, user_type,
                   allowed_profiles, allowed_origins, rate_limit_per_min,
                   expires_at, tenant_id
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

        return UserContext(
            user_id=row["user_id"] or "api-user",
            user_role=row["user_role"],
            security_level_max=row["security_level_max"],
            user_type=row["user_type"] or "",
            allowed_profiles=row["allowed_profiles"] or [],
            allowed_origins=row["allowed_origins"] or [],
            rate_limit_per_min=row["rate_limit_per_min"] or 60,
            tenant_id=row["tenant_id"],
        )

    async def check_profile_access(
        self,
        user_ctx: UserContext,
        chatbot_id: str,
    ) -> None:
        """사용자가 해당 프로필에 접근 권한이 있는지 확인한다."""
        if not self._auth_required:
            return

        # allowed_profiles가 비어있으면 모든 프로필 접근 허용
        if user_ctx.allowed_profiles and chatbot_id not in user_ctx.allowed_profiles:
            raise AuthError(
                f"이 API Key는 '{chatbot_id}' 프로필에 접근 권한이 없습니다"
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

        raw_key, key_hash = generate_api_key()

        await self._pool.execute(
            """
            INSERT INTO api_keys (key_hash, name, user_id, user_role, security_level_max,
                                  allowed_profiles, allowed_origins, rate_limit_per_min,
                                  user_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            key_hash, name, creator_user_id, user_role, security_level_max,
            allowed_profiles or [], allowed_origins or [], rate_limit_per_min,
            user_type,
        )

        logger.info("api_key_created", name=name, user_role=user_role, origins=allowed_origins or [])
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
