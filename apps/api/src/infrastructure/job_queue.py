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
            SET status = 'processing', locked_by = $2, locked_at = NOW(),
                lease_expires_at = NOW() + interval '180 seconds'
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

    async def get_latest_job_by_document(
        self, queue_names: list[str], document_id: str,
    ) -> Optional[dict]:
        """문서 ID 로 최신 잡 1건 조회 (KMS 워치독의 정합 확인용).

        payload 의 document_id 또는 source_document_id 매칭. 반환 필드는
        상태 판단에 필요한 최소만 노출한다.
        """
        row = await self._pool.fetchrow(
            """
            SELECT id, queue_name, status, attempts, max_attempts,
                   last_error, created_at, completed_at
            FROM job_queue
            WHERE queue_name = ANY($1::text[])
              AND (payload->>'document_id' = $2
                   OR payload->>'source_document_id' = $2)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            queue_names, document_id,
        )
        if not row:
            return None
        return {
            "job_id": str(row["id"]),
            "queue_name": row["queue_name"],
            "status": row["status"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "last_error": row["last_error"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        }

    async def has_active_job(self, queue_name: str, document_id: str) -> Optional[str]:
        """해당 문서의 pending/processing 잡이 있으면 job_id 반환 (중복 큐잉 방지)."""
        row = await self._pool.fetchrow(
            """
            SELECT id FROM job_queue
            WHERE queue_name = $1
              AND status IN ('pending', 'processing')
              AND payload->>'document_id' = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            queue_name, document_id,
        )
        return str(row["id"]) if row else None

    async def extend_lease(self, job_id: str, seconds: int = 180) -> None:
        """실행 중 잡의 lease 연장 (하트비트). 수십 분짜리 정상 잡을 보호한다."""
        await self._pool.execute(
            """
            UPDATE job_queue
            SET lease_expires_at = NOW() + make_interval(secs => $2)
            WHERE id = $1 AND status = 'processing'
            """,
            uuid.UUID(job_id), float(seconds),
        )

    async def cleanup_stale(self, stale_seconds: int = 600) -> int:
        """lease 가 만료된 processing 작업을 pending으로 복구.

        하트비트(extend_lease)가 도는 정상 잡은 lease 가 계속 갱신되므로
        절대 회수되지 않는다 — 크래시/강제종료된 워커의 잡만 lease 만료
        (≤3분) 후 회수된다. 마이그레이션 이전의 구 행(lease NULL)은
        locked_at + stale_seconds 폴백으로 처리.
        """
        result = await self._pool.execute(
            """
            UPDATE job_queue
            SET status = 'pending', locked_by = NULL, locked_at = NULL,
                lease_expires_at = NULL
            WHERE status = 'processing'
              AND COALESCE(
                    lease_expires_at,
                    locked_at + make_interval(secs => $1)
                  ) < NOW()
            """,
            float(stale_seconds),
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

    async def _heartbeat(self, job_id: str, interval: float = 60.0) -> None:
        """실행 중 lease 를 주기 연장 — cleanup_stale 의 오회수를 방지한다."""
        try:
            while True:
                await asyncio.sleep(interval)
                await self._queue.extend_lease(job_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # 하트비트 실패가 잡 자체를 죽이면 안 됨
            logger.warning("Job %s heartbeat failed: %s", job_id, e)

    async def _process_job(self, job: dict) -> None:
        job_id = job["id"]
        heartbeat = asyncio.create_task(self._heartbeat(job_id))
        try:
            payload = job["payload"]
            payload["job_id"] = str(job_id)
            # 핸들러가 "마지막 시도인지"를 판단할 수 있게 시도 정보를 주입한다
            # (예: vlm_enhance 는 마지막 시도 실패 시에만 KMS 에 FAILED 보고).
            payload["job_attempts"] = job.get("attempts", 0)
            payload["job_max_attempts"] = job.get("max_attempts", 3)
            result = await self._handler(payload)
            await self._queue.complete(job_id, result=result)
        except Exception as e:
            # str(e) 가 빈 예외(TimeoutError 류)는 무음 실패가 된다 — 타입명을
            # 항상 포함해 last_error/로그에서 원인을 식별 가능하게 한다.
            error_desc = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.error("Job %s failed: %s", job_id, error_desc, exc_info=True)
            await self._queue.fail(job_id, error_desc)
        finally:
            heartbeat.cancel()
