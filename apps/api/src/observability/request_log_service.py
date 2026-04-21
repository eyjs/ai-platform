"""요청 로그 서비스.

Gateway 가 fire-and-forget 으로 enqueue, 워커가 배치로 DB insert.
레이턴시 영향 0 목표.

- enqueue(): sync, non-blocking. queue full 시 drop + warning.
- _flush_loop(): batch_size 또는 flush_interval_ms 마다 INSERT 다건.
- stop(): 남은 큐 flush 후 종료.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .request_log_models import RequestLogEntry

logger = logging.getLogger(__name__)


SessionFactory = Callable[[], "AsyncSession"]   # async context manager factory


class RequestLogService:
    def __init__(
        self,
        session_factory: SessionFactory,
        batch_size: int = 50,
        flush_interval_ms: int = 500,
        max_queue: int = 10000,
    ):
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_ms / 1000.0
        self._max_queue = max_queue

        self._queue: asyncio.Queue[RequestLogEntry] = asyncio.Queue(maxsize=max_queue)
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = False

    def enqueue(self, entry: RequestLogEntry) -> None:
        """sync. 비차단. queue full 시 drop."""
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            logger.warning("request_log.drop reason=queue_full")
        except Exception as e:
            # 조용히 삼키지 않되, 요청 플로우는 중단하지 않는다.
            logger.warning("request_log.enqueue_failed error=%s", e)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._flush_loop(), name="request_log.flush")
        logger.info("request_log.service.started")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("request_log.shutdown.timeout — cancelling")
                self._task.cancel()
        # 남은 큐 flush
        pending: list[RequestLogEntry] = []
        while not self._queue.empty():
            try:
                pending.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if pending:
            await self._flush_batch(pending)
        self._task = None
        logger.info("request_log.service.stopped drained=%d", len(pending))

    async def _flush_loop(self) -> None:
        while not self._stopping:
            batch: list[RequestLogEntry] = []
            try:
                # 첫 entry 는 interval 까지 대기
                first = await asyncio.wait_for(self._queue.get(), timeout=self._flush_interval_s)
                batch.append(first)
                # 배치 채우기 (non-blocking)
                while len(batch) < self._batch_size:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.warning("request_log.loop.error error=%s", e)
                continue

            if batch:
                await self._flush_batch(batch)

    async def _flush_batch(self, batch: list[RequestLogEntry]) -> None:
        if not batch:
            return
        try:
            async with self._session_factory() as session:   # type: ignore[misc]
                await session.execute(
                    text(
                        """
                        INSERT INTO api_request_logs
                          (ts, api_key_id, profile_id, provider_id, status_code, latency_ms,
                           prompt_tokens, completion_tokens, cache_hit, error_code,
                           request_preview, response_preview,
                           response_id, faithfulness_score)
                        VALUES
                          (:ts, :api_key_id, :profile_id, :provider_id, :status_code, :latency_ms,
                           :prompt_tokens, :completion_tokens, :cache_hit, :error_code,
                           :request_preview, :response_preview,
                           :response_id, :faithfulness_score)
                        """
                    ),
                    [
                        {
                            "ts": e.ts,
                            "api_key_id": e.api_key_id,
                            "profile_id": e.profile_id,
                            "provider_id": e.provider_id,
                            "status_code": e.status_code,
                            "latency_ms": e.latency_ms,
                            "prompt_tokens": e.prompt_tokens,
                            "completion_tokens": e.completion_tokens,
                            "cache_hit": e.cache_hit,
                            "error_code": e.error_code,
                            "request_preview": RequestLogEntry.truncate_preview(e.request_preview),
                            "response_preview": RequestLogEntry.truncate_preview(e.response_preview),
                            "response_id": e.response_id,
                            "faithfulness_score": e.faithfulness_score,
                        }
                        for e in batch
                    ],
                )
                await session.commit()
            logger.debug("request_log.flush count=%d", len(batch))
        except Exception as e:
            # 데이터 손실은 최소화하되 요청 플로우는 영향 없음
            logger.warning("request_log.flush.error count=%d error=%s", len(batch), e)
