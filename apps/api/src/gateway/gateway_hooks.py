"""Gateway 요청 플로우 통합 훅 (Task 009).

Gateway /chat, /chat/stream 엔드포인트에서 호출하는 helper.
- request log enqueue (fire-and-forget)
- response cache get/put
- provider router resolve/invoke

이 모듈은 순수 helper 로, router.py 의 침습적 변경을 최소화한다.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Awaitable, Callable, Optional

from src.observability.request_log_models import RequestLogEntry
from src.observability.request_log_service import RequestLogService
from src.services.response_cache import ResponseCacheService
from src.services.response_cache_models import normalize_input

logger = logging.getLogger(__name__)


@contextmanager
def latency_timer():
    start = time.monotonic()
    container: dict[str, Any] = {"elapsed_ms": 0}
    try:
        yield container
    finally:
        container["elapsed_ms"] = int((time.monotonic() - start) * 1000)


def safe_enqueue(
    svc: Optional[RequestLogService],
    entry: RequestLogEntry,
) -> None:
    """RequestLog enqueue 는 절대 요청 플로우를 중단하지 않는다.

    R1 보장: 반드시 finally 블록 또는 BackgroundTasks 에서만 호출.
    await 금지. 큐 full 시 drop + warning.
    """
    if svc is None:
        return
    try:
        svc.enqueue(entry)
    except Exception as e:
        logger.warning("request_log.enqueue.swallowed error=%s", e)


async def try_cache_get(
    svc: Optional[ResponseCacheService],
    profile_id: str,
    mode: str,
    prompt: str,
) -> Optional[str]:
    """Cache lookup. 실패 시 None 반환, 요청 플로우는 계속."""
    if svc is None:
        return None
    try:
        norm = normalize_input(prompt)
        cached = await svc.get(profile_id, mode, norm)
        return cached.response_text if cached else None
    except Exception as e:
        logger.warning("cache.get.hook_error error=%s", e)
        return None


async def try_cache_put(
    svc: Optional[ResponseCacheService],
    profile_id: str,
    mode: str,
    prompt: str,
    response: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    ttl_seconds: Optional[int] = None,
) -> None:
    if svc is None:
        return
    try:
        norm = normalize_input(prompt)
        await svc.put(profile_id, mode, norm, response, prompt_tokens, completion_tokens, ttl_seconds)
    except Exception as e:
        logger.warning("cache.put.hook_error error=%s", e)


def should_use_cache(profile: Any, mode: str, cache_svc: Optional[ResponseCacheService]) -> bool:
    if cache_svc is None:
        return False
    try:
        return cache_svc.should_cache(profile, mode)
    except Exception:
        return False


async def subscribe_profile_updates(
    profile_store: Any,
    cache_svc: Optional[ResponseCacheService],
    dsn: str,
) -> None:
    """profile_updated NOTIFY 를 LISTEN 하여 cache 무효화.

    asyncpg 전용 LISTEN. profile_store 는 동일 이벤트로 reload.
    """
    import asyncpg

    async def _listen():
        try:
            conn = await asyncpg.connect(dsn=dsn)
        except Exception as e:
            logger.warning("profile_listen.connect_failed error=%s", e)
            return

        async def _handler(conn_, pid, channel, payload):
            logger.info("profile_updated.received profile_id=%s", payload)
            try:
                if hasattr(profile_store, "reload_one"):
                    await profile_store.reload_one(payload)
                elif hasattr(profile_store, "load_seeds"):
                    await profile_store.load_seeds()
            except Exception as e:
                logger.warning("profile_store.reload_failed error=%s", e)
            if cache_svc is not None:
                try:
                    removed = await cache_svc.invalidate_profile(payload)
                    logger.info("cache.invalidated profile_id=%s removed=%d", payload, removed)
                except Exception as e:
                    logger.warning("cache.invalidate_failed error=%s", e)

        try:
            await conn.add_listener("profile_updated", _handler)
            # 연결 유지
            while True:
                import asyncio
                await asyncio.sleep(60)
        except Exception as e:
            logger.warning("profile_listen.error error=%s", e)
        finally:
            try:
                await conn.close()
            except Exception:
                pass

    import asyncio
    return asyncio.create_task(_listen(), name="profile.listen")
