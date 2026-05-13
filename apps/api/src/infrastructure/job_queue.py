"""PostgreSQL SKIP LOCKED 기반 작업 큐.

Redis/BullMQ 대체 — job_queue 테이블 사용.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Optional

import asyncpg

logger = logging.getLogger(__name__)


class JobQueue:
    """PostgreSQL SKIP LOCKED 기반 작업 큐."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def enqueue(
        self,
        queue_name: str,
        payload: dict,
        priority: int = 0,
        max_attempts: int = 3,
        delay_seconds: int = 0,
    ) -> str:
        """작업 추가."""
        job_id = str(uuid.uuid4())
        scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        await self._pool.execute(
            """
            INSERT INTO job_queue (id, queue_name, payload, priority, max_attempts, scheduled_at)
            VALUES ($1, $2, $3::jsonb, $4, $5, $6)
            """,
            uuid.UUID(job_id), queue_name,
            json.dumps(payload, ensure_ascii=False),
            priority, max_attempts, scheduled_at,
        )
        logger.info("Enqueued job %s to %s", job_id, queue_name)
        return job_id

    async def dequeue(
        self, queue_name: str, worker_id: str,
    ) -> Optional[dict]:
        """SKIP LOCKED으로 작업 가져오기 (경합 없음)."""
        row = await self._pool.fetchrow(
            """
            UPDATE job_queue
            SET status = 'processing', locked_by = $2, locked_at = NOW()
            WHERE id = (
                SELECT id FROM job_queue
                WHERE queue_name = $1
                  AND status = 'pending'
                  AND scheduled_at <= NOW()
                ORDER BY priority DESC, scheduled_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, queue_name, payload, attempts, max_attempts
            """,
            queue_name, worker_id,
        )
        if not row:
            return None
        return {
            "id": str(row["id"]),
            "queue_name": row["queue_name"],
            "payload": json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
        }

    async def complete(self, job_id: str, result: Optional[dict] = None) -> None:
        await self._pool.execute(
            """
            UPDATE job_queue
            SET status = 'completed', completed_at = NOW(), locked_by = NULL,
                result = $2::jsonb
            WHERE id = $1
            """,
            uuid.UUID(job_id),
            json.dumps(result, ensure_ascii=False) if result else None,
        )

    async def fail(self, job_id: str, error: str) -> None:
        """실패 처리. 재시도 가능하면 pending으로 복귀 (지수 백오프)."""
        row = await self._pool.fetchrow(
            "SELECT attempts, max_attempts FROM job_queue WHERE id = $1",
            uuid.UUID(job_id),
        )
        if not row:
            return

        attempts = row["attempts"] + 1
        if attempts < row["max_attempts"]:
            delay = 30 * (2 ** (attempts - 1))  # 30s, 60s, 120s
            scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            await self._pool.execute(
                """
                UPDATE job_queue
                SET status = 'pending', attempts = $2, last_error = $3,
                    locked_by = NULL, locked_at = NULL, scheduled_at = $4
                WHERE id = $1
                """,
                uuid.UUID(job_id), attempts, error, scheduled_at,
            )
            logger.info("Job %s retry %d/%d (delay %ds)", job_id, attempts, row["max_attempts"], delay)
        else:
            await self._pool.execute(
                """
                UPDATE job_queue
                SET status = 'failed', attempts = $2, last_error = $3,
                    locked_by = NULL, completed_at = NOW()
                WHERE id = $1
                """,
                uuid.UUID(job_id), attempts, error,
            )
            logger.warning("Job %s permanently failed after %d attempts", job_id, attempts)

    async def get_job(self, job_id: str) -> Optional[dict]:
        """작업 상태를 조회한다."""
        row = await self._pool.fetchrow(
            """
            SELECT id, queue_name, payload, status, attempts, max_attempts,
                   last_error, result, created_at, completed_at
            FROM job_queue WHERE id = $1
            """,
            uuid.UUID(job_id),
        )
        if not row:
            return None
        result_raw = row["result"]
        return {
            "id": str(row["id"]),
            "queue_name": row["queue_name"],
            "payload": json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
            "status": row["status"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "last_error": row["last_error"],
            "result": json.loads(result_raw) if isinstance(result_raw, str) else result_raw,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        }

    async def cleanup_stale(self, stale_seconds: int = 600) -> int:
        """오래된 processing 작업을 pending으로 복구 (워커 비정상 종료 대응)."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
        result = await self._pool.execute(
            """
            UPDATE job_queue
            SET status = 'pending', locked_by = NULL, locked_at = NULL
            WHERE status = 'processing' AND locked_at < $1
            """,
            cutoff,
        )
        count = int(result.split()[-1])
        if count > 0:
            logger.info("Recovered %d stale jobs", count)
        return count


class QueueWorker:
    """큐 워커 — 백그라운드 폴링으로 작업 처리."""

    def __init__(
        self,
        queue: JobQueue,
        queue_name: str,
        handler: Callable[[dict], Coroutine[Any, Any, None]],
        worker_id: Optional[str] = None,
        poll_interval: float = 1.0,
        max_concurrent: int = 5,
    ):
        self._queue = queue
        self._queue_name = queue_name
        self._handler = handler
        self._worker_id = worker_id or str(uuid.uuid4())[:8]
        self._poll_interval = poll_interval
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = False
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._running = True
        logger.info("QueueWorker[%s] started for %s", self._worker_id, self._queue_name)
        while self._running:
            async with self._semaphore:
                job = await self._queue.dequeue(self._queue_name, self._worker_id)
            if job:
                task = asyncio.create_task(self._process_job(job))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            else:
                await asyncio.sleep(self._poll_interval)

    async def stop(self, timeout: float = 30.0) -> None:
        self._running = False
        if self._tasks:
            logger.info("QueueWorker[%s] draining %d tasks", self._worker_id, len(self._tasks))
            await asyncio.wait(self._tasks, timeout=timeout)
        logger.info("QueueWorker[%s] stopped", self._worker_id)

    async def _process_job(self, job: dict) -> None:
        job_id = job["id"]
        try:
            payload = job["payload"]
            payload["job_id"] = str(job_id)
            result = await self._handler(payload)
            await self._queue.complete(job_id, result=result)
        except Exception as e:
            logger.error("Job %s failed: %s", job_id, e)
            await self._queue.fail(job_id, str(e))
