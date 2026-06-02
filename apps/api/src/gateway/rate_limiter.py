"""PostgreSQL Token Bucket Rate Limiter.

Redis 없이 단일 원자적 UPSERT로 동시성 제어 (행 락 없음, B6).
레이트리밋 축은 API 키 + 세션(또는 visitor) 복합키 (B5) — 공유키 사용자가
한 버킷을 공유해 서로의 쿼터를 소진시키던 문제를 제거한다.
"""

import math
from typing import Tuple

import asyncpg
from fastapi import HTTPException
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from src.gateway.models import UserContext
from src.observability.logging import get_logger

logger = get_logger(__name__)

# 기본값: 분당 60회 (초당 1개 충전, 최대 60개 버스트)
DEFAULT_CAPACITY = 60
DEFAULT_REFILL_RATE = 1.0

# 복합키의 세션/visitor 부분 최대 길이 (클라이언트 제공 session_id 남용 방지)
MAX_SUBKEY_LEN = 128


def build_client_id(
    user_ctx: UserContext,
    *,
    sub_key: str | None = None,
    fallback: str = "anonymous",
) -> str:
    """레이트리밋 버킷 키를 만든다 (B5).

    축: API 키별로 분리하되, 같은 공유키 내에서는 세션(또는 미래의 visitor)별로
    버킷을 나눈다. JWT 사용자는 서명된 user_id가 곧 정체성이므로 그대로 base가 된다.

    sub_key(클라이언트 제공 session_id 등)는 길이를 제한해 PK 비대화를 막는다.
    """
    base = user_ctx.api_key_id or user_ctx.user_id or fallback
    if sub_key:
        return f"{base}:{sub_key[:MAX_SUBKEY_LEN]}"
    return base


class PGRateLimiter:
    """PostgreSQL 기반 Token Bucket Rate Limiter.

    capacity: 버킷 최대 토큰 수 (버스트 허용량)
    refill_rate: 초당 충전 토큰 수
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def acquire(
        self,
        client_id: str,
        cost: int = 1,
        capacity: float = DEFAULT_CAPACITY,
        refill_rate: float = DEFAULT_REFILL_RATE,
    ) -> Tuple[bool, float]:
        """토큰을 소비한다. 단일 원자적 UPSERT — 행 락(FOR UPDATE) 없음 (B6).

        ON CONFLICT ... WHERE: 충전 후 토큰이 cost 이상일 때만 차감/갱신한다.
        부족하면 행을 갱신하지 않으므로 RETURNING이 0행 → 거부로 판정.

        Returns:
            (허용 여부, 남은 토큰 수)
        """
        row = await self._pool.fetchrow(
            """
            INSERT INTO api_rate_limits AS arl (client_id, tokens, last_updated)
            VALUES ($1, $2::float8 - $3::float8, NOW())
            ON CONFLICT (client_id) DO UPDATE
            SET tokens = LEAST($2::float8, arl.tokens
                         + EXTRACT(EPOCH FROM (NOW() - arl.last_updated))::float8 * $4::float8)
                         - $3::float8,
                last_updated = NOW()
            WHERE LEAST($2::float8, arl.tokens
                  + EXTRACT(EPOCH FROM (NOW() - arl.last_updated))::float8 * $4::float8) >= $3::float8
            RETURNING tokens
            """,
            client_id,
            capacity,
            cost,
            refill_rate,
        )

        if row is not None:
            return True, float(row["tokens"])

        # 거부: 행을 갱신하지 않았다. Retry-After 계산을 위해 현재 충전량만 비잠금 조회.
        current = await self._pool.fetchval(
            """
            SELECT LEAST($2::float8, tokens
                   + EXTRACT(EPOCH FROM (NOW() - last_updated))::float8 * $3::float8)
            FROM api_rate_limits
            WHERE client_id = $1
            """,
            client_id,
            capacity,
            refill_rate,
        )
        return False, float(current if current is not None else 0.0)

    async def verify_request(
        self,
        client_id: str,
        rate_limit_per_min: int = DEFAULT_CAPACITY,
    ) -> None:
        """요청을 검증한다. 초과 시 429 반환.

        rate_limit_per_min으로 per-client capacity/refill_rate를 계산.
        """
        capacity = float(rate_limit_per_min)
        refill_rate = rate_limit_per_min / 60.0

        allowed, remaining = await self.acquire(
            client_id=client_id,
            cost=1,
            capacity=capacity,
            refill_rate=refill_rate,
        )

        if not allowed:
            tokens_needed = 1 - remaining
            retry_after = max(1, math.ceil(tokens_needed / refill_rate)) if refill_rate > 0 else 1

            logger.warning(
                "rate_limit_exceeded",
                layer="GATEWAY",
                client_id=client_id,
                remaining=round(remaining, 2),
                capacity=rate_limit_per_min,
                retry_after=retry_after,
            )
            raise HTTPException(
                status_code=HTTP_429_TOO_MANY_REQUESTS,
                detail="Too Many Requests. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    async def cleanup_stale(self, idle_seconds: int = 3600) -> int:
        """유휴 버킷을 삭제한다 (B5 복합키로 늘어난 카디널리티 관리).

        idle_seconds 이상 미사용 버킷만 삭제한다. 이만큼 유휴면 어떤 capacity든
        이미 만석으로 충전됐을 것이므로, 삭제 후 재생성돼도 만석에서 시작 = 안전.
        (충전 미완료 버킷을 지우면 throttled 클라이언트가 리셋될 수 있어 금지.)
        """
        result = await self._pool.execute(
            "DELETE FROM api_rate_limits WHERE last_updated < NOW() - make_interval(secs => $1)",
            idle_seconds,
        )
        # asyncpg execute는 "DELETE <n>" 문자열을 반환
        try:
            deleted = int(result.split()[-1])
        except (ValueError, IndexError, AttributeError):
            deleted = 0
        if deleted:
            logger.info("rate_limit_buckets_cleaned", deleted=deleted, idle_seconds=idle_seconds)
        return deleted
