"""job_queue lease 기반 visibility timeout 테스트 (I/F 결함 Fix 2).

검증 계약:
  - dequeue 가 lease_expires_at 을 설정한다
  - extend_lease 는 processing 상태에서만 lease 를 연장한다
  - cleanup_stale 은 lease 만료(구 행은 locked_at 폴백)만 회수한다
  - _process_job 이 하트비트 태스크를 돌리고 종료 시 취소한다
  - has_active_job 이 pending/processing 잡을 문서 ID 로 찾는다
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.job_queue import JobQueue, QueueWorker


def _make_queue() -> tuple[JobQueue, AsyncMock]:
    pool = AsyncMock()
    queue = JobQueue(pool)
    return queue, pool


class TestLeaseSql:
    @pytest.mark.asyncio
    async def test_dequeue_sets_lease(self):
        queue, pool = _make_queue()
        pool.fetchrow.return_value = None
        await queue.dequeue("vlm_enhance", "w1")
        sql = pool.fetchrow.call_args.args[0]
        assert "lease_expires_at = NOW() + interval '180 seconds'" in sql

    @pytest.mark.asyncio
    async def test_extend_lease_targets_processing_only(self):
        queue, pool = _make_queue()
        job_id = str(uuid.uuid4())
        await queue.extend_lease(job_id, seconds=180)
        sql = pool.execute.call_args.args[0]
        assert "status = 'processing'" in sql
        assert "lease_expires_at = NOW() + make_interval" in sql

    @pytest.mark.asyncio
    async def test_cleanup_reclaims_only_expired_lease(self):
        queue, pool = _make_queue()
        pool.execute.return_value = "UPDATE 2"
        count = await queue.cleanup_stale(stale_seconds=600)
        assert count == 2
        sql = pool.execute.call_args.args[0]
        # lease 우선, 구 행(NULL)은 locked_at 폴백
        assert "COALESCE" in sql and "lease_expires_at" in sql and "locked_at" in sql

    @pytest.mark.asyncio
    async def test_has_active_job(self):
        queue, pool = _make_queue()
        jid = uuid.uuid4()
        pool.fetchrow.return_value = {"id": jid}
        assert await queue.has_active_job("vlm_enhance", "doc-1") == str(jid)
        sql = pool.fetchrow.call_args.args[0]
        assert "status IN ('pending', 'processing')" in sql
        assert "payload->>'document_id'" in sql

        pool.fetchrow.return_value = None
        assert await queue.has_active_job("vlm_enhance", "doc-2") is None


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_process_job_runs_and_cancels_heartbeat(self):
        queue = AsyncMock()
        started = asyncio.Event()

        async def handler(payload: dict) -> dict:
            started.set()
            await asyncio.sleep(0.25)
            return {"ok": True}

        worker = QueueWorker(queue=queue, queue_name="q", handler=handler)
        # 하트비트 주기를 짧게: 잡 실행(0.25s) 동안 최소 1회 연장돼야 함
        original = worker._heartbeat

        async def fast_heartbeat(job_id: str, interval: float = 60.0):
            await original(job_id, interval=0.05)

        worker._heartbeat = fast_heartbeat  # type: ignore[method-assign]

        await worker._process_job(
            {"id": str(uuid.uuid4()), "payload": {}, "attempts": 0, "max_attempts": 3},
        )
        assert started.is_set()
        assert queue.extend_lease.await_count >= 1
        queue.complete.assert_awaited_once()
        # 잡 종료 후 하트비트가 계속 돌면 안 됨
        count_after = queue.extend_lease.await_count
        await asyncio.sleep(0.15)
        assert queue.extend_lease.await_count == count_after

    @pytest.mark.asyncio
    async def test_heartbeat_failure_does_not_kill_job(self):
        queue = AsyncMock()
        queue.extend_lease.side_effect = RuntimeError("db down")

        async def handler(payload: dict) -> dict:
            await asyncio.sleep(0.15)
            return {"ok": True}

        worker = QueueWorker(queue=queue, queue_name="q", handler=handler)
        original = worker._heartbeat

        async def fast_heartbeat(job_id: str, interval: float = 60.0):
            await original(job_id, interval=0.05)

        worker._heartbeat = fast_heartbeat  # type: ignore[method-assign]
        await worker._process_job(
            {"id": str(uuid.uuid4()), "payload": {}, "attempts": 0, "max_attempts": 3},
        )
        queue.complete.assert_awaited_once()  # 잡은 정상 완료
