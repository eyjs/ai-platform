"""Response Cache Service (PostgreSQL 기반).

- PostgreSQL 단일 스택. Redis 금지.
- deterministic 모드만 기본 캐시. agentic 은 profile 에서 opt-in.
- TTL 만료는 주기적 DELETE sweeper.

Contract: .pipeline/contracts/response-cache-schema.md
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import text

from .response_cache_models import CachedResponse, compute_cache_key, normalize_input

logger = logging.getLogger(__name__)


class ResponseCacheService:
    def __init__(
        self,
        session_factory: Callable[[], Any],
        default_ttl_seconds: int = 3600,
    ):
        self._session_factory = session_factory
        self._default_ttl = default_ttl_seconds
        self._sweeper_task: Optional[asyncio.Task[None]] = None
        self._sweeper_stopped = True

    # ---- Public API ----

    def should_cache(self, profile: Any, mode: str) -> bool:
        """Contract should_cache 논리.

        profile 은 AgentProfile 또는 dict. config.cache 내부 접근.
        """
        cache_cfg: dict[str, Any] | None = None
        try:
            if profile is None:
                cache_cfg = None
            elif hasattr(profile, "config") and isinstance(profile.config, dict):
                cache_cfg = profile.config.get("cache")
            elif isinstance(profile, dict):
                cache_cfg = profile.get("cache")
        except Exception:
            cache_cfg = None

        if cache_cfg is None:
            return mode == "deterministic"
        if cache_cfg.get("enabled") is False:
            return False
        if mode == "deterministic":
            return True
        if mode == "agentic":
            return bool(cache_cfg.get("agentic_enabled", False))
        return False

    async def get(
        self, profile_id: str, mode: str, normalized: str
    ) -> Optional[CachedResponse]:
        key = compute_cache_key(profile_id, mode, normalized)
        try:
            async with self._session_factory() as session:
                row = (await session.execute(
                    text(
                        """
                        SELECT cache_key, profile_id, mode, response_text,
                               prompt_tokens, completion_tokens,
                               created_at, expires_at, hit_count
                        FROM response_cache
                        WHERE cache_key = :k AND expires_at > NOW()
                        """
                    ),
                    {"k": key},
                )).mappings().first()

                if row is None:
                    return None

                # hit_count 업데이트는 non-blocking (별도 task)
                asyncio.create_task(self._increment_hit(key))
                return CachedResponse(
                    cache_key=row["cache_key"],
                    profile_id=row["profile_id"],
                    mode=row["mode"],
                    response_text=row["response_text"],
                    prompt_tokens=row["prompt_tokens"] or 0,
                    completion_tokens=row["completion_tokens"] or 0,
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                    hit_count=row["hit_count"] or 0,
                )
        except Exception as e:
            logger.warning("cache.get.error key=%s error=%s", key[:8], e)
            return None

    async def put(
        self,
        profile_id: str,
        mode: str,
        normalized: str,
        response_text: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        key = compute_cache_key(profile_id, mode, normalized)
        ttl = ttl_seconds or self._default_ttl
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

        try:
            async with self._session_factory() as session:
                await session.execute(
                    text(
                        """
                        INSERT INTO response_cache
                          (cache_key, profile_id, mode, response_text,
                           prompt_tokens, completion_tokens, expires_at)
                        VALUES (:k, :pid, :mode, :rt, :pt, :ct, :exp)
                        ON CONFLICT (cache_key) DO UPDATE
                          SET response_text = EXCLUDED.response_text,
                              prompt_tokens = EXCLUDED.prompt_tokens,
                              completion_tokens = EXCLUDED.completion_tokens,
                              expires_at = EXCLUDED.expires_at,
                              created_at = NOW()
                        """
                    ),
                    {
                        "k": key, "pid": profile_id, "mode": mode, "rt": response_text,
                        "pt": prompt_tokens, "ct": completion_tokens, "exp": expires_at,
                    },
                )
                await session.commit()
        except Exception as e:
            logger.warning("cache.put.error key=%s error=%s", key[:8], e)

    async def invalidate_profile(self, profile_id: str) -> int:
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    text("DELETE FROM response_cache WHERE profile_id = :pid"),
                    {"pid": profile_id},
                )
                await session.commit()
                return int(result.rowcount or 0)
        except Exception as e:
            logger.warning("cache.invalidate.error profile=%s error=%s", profile_id, e)
            return 0

    # ---- Sweeper ----

    async def start_sweeper(self, interval_seconds: int = 60) -> None:
        if self._sweeper_task is not None:
            return
        self._sweeper_stopped = False
        self._sweeper_task = asyncio.create_task(
            self._sweep_loop(interval_seconds), name="cache.sweeper",
        )
        logger.info("cache.sweeper.started interval=%ds", interval_seconds)

    async def stop_sweeper(self) -> None:
        self._sweeper_stopped = True
        if self._sweeper_task is not None:
            try:
                await asyncio.wait_for(self._sweeper_task, timeout=3.0)
            except asyncio.TimeoutError:
                self._sweeper_task.cancel()
            self._sweeper_task = None
        logger.info("cache.sweeper.stopped")

    async def _sweep_loop(self, interval: int) -> None:
        while not self._sweeper_stopped:
            try:
                async with self._session_factory() as session:
                    result = await session.execute(
                        text("DELETE FROM response_cache WHERE expires_at < NOW()"),
                    )
                    await session.commit()
                    removed = int(result.rowcount or 0)
                    if removed > 0:
                        logger.debug("cache.sweep.removed count=%d", removed)
            except Exception as e:
                logger.warning("cache.sweep.error error=%s", e)
            await asyncio.sleep(interval)

    async def _increment_hit(self, cache_key: str) -> None:
        try:
            async with self._session_factory() as session:
                await session.execute(
                    text(
                        "UPDATE response_cache "
                        "SET hit_count = hit_count + 1, last_hit_at = NOW() "
                        "WHERE cache_key = :k"
                    ),
                    {"k": cache_key},
                )
                await session.commit()
        except Exception as e:
            logger.debug("cache.hit_count.error error=%s", e)
