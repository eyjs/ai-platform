"""PostgreSQL Token Bucket Rate Limiter.

Redis 없이 SELECT FOR UPDATE로 원자적 동시성 제어.
UserContext.rate_limit_per_min 기반 per-client 제한.
"""

import math
from typing import Tuple

import asyncpg
from fastapi import HTTPException
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from src.observability.logging import get_logger

logger = get_logger(__name__)

# 기본값: 분당 60회 (초당 1개 충전, 최대 60개 버스트)
DEFAULT_CAPACITY = 60
DEFAULT_REFILL_RATE = 1.0


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
        """토큰을 소비한다. FOR UPDATE로 동시성 제어.

        Returns:
            (허용 여부, 남은 토큰 수)
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT tokens,
                           EXTRACT(EPOCH FROM (NOW() - last_updated)) AS elapsed
                    FROM api_rate_limits
                    WHERE client_id = $1
                    FOR UPDATE
                    """,
                    client_id,
                )

                if not row:
                    remaining = capacity - cost
                    await conn.execute(
                        """
                        INSERT INTO api_rate_limits (client_id, tokens, last_updated)
                        VALUES ($1, $2, NOW())
                        ON CONFLICT (client_id) DO UPDATE
                        SET tokens = LEAST($3, api_rate_limits.tokens
                                     + EXTRACT(EPOCH FROM (NOW() - api_rate_limits.last_updated)) * $4)
                                     - $5,
                            last_updated = NOW()
                        """,
                        client_id,
                        remaining,
                        capacity,
                        refill_rate,
                        cost,
                    )
                    return True, remaining

                # 토큰 충전: 경과 시간 * 충전율, capacity 상한
                current_tokens = min(
                    capacity,
                    row["tokens"] + (row["elapsed"] * refill_rate),
                )

                if current_tokens >= cost:
                    remaining = current_tokens - cost
                    await conn.execute(
                        "UPDATE api_rate_limits SET tokens = $1, last_updated = NOW() WHERE client_id = $2",
                        remaining,
                        client_id,
                    )
                    return True, remaining
                else:
                    # 거부: 충전 상태만 갱신 (토큰 차감 안 함)
                    await conn.execute(
                        "UPDATE api_rate_limits SET tokens = $1, last_updated = NOW() WHERE client_id = $2",
                        current_tokens,
                        client_id,
                    )
                    return False, current_tokens

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
            retry_after = math.ceil(tokens_needed / refill_rate)

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
