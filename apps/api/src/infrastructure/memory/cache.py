"""PostgreSQL UNLOGGED TABLE 기반 캐시.

Redis 대체 — cache_entries 테이블 사용 (WAL 없음, 빠른 쓰기).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

logger = logging.getLogger(__name__)


class PgCache:
    """PostgreSQL UNLOGGED TABLE 기반 캐시."""

    def __init__(self, pool: asyncpg.Pool, default_ttl_seconds: int = 300):
        self._pool = pool
        self._default_ttl = default_ttl_seconds

    async def get(self, key: str) -> Optional[Any]:
        row = await self._pool.fetchrow(
            "SELECT value FROM cache_entries WHERE key = $1 AND expires_at > NOW()",
            key,
        )
        if not row:
            return None
        return json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]

    async def set(
        self, key: str, value: Any, ttl_seconds: Optional[int] = None,
    ) -> None:
        ttl = ttl_seconds or self._default_ttl
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        value_json = json.dumps(value, ensure_ascii=False)
        await self._pool.execute(
            """
            INSERT INTO cache_entries (key, value, expires_at)
            VALUES ($1, $2::jsonb, $3)
            ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, expires_at = $3
            """,
            key, value_json, expires_at,
        )

    async def delete(self, key: str) -> None:
        await self._pool.execute("DELETE FROM cache_entries WHERE key = $1", key)

    async def cleanup_expired(self) -> int:
        result = await self._pool.execute(
            "DELETE FROM cache_entries WHERE expires_at < NOW()"
        )
        count = int(result.split()[-1])
        if count > 0:
            logger.info("Cleaned up %d expired cache entries", count)
        return count
