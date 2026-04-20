"""RequestLogService 단위 테스트."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.observability.request_log_models import RequestLogEntry
from src.observability.request_log_service import RequestLogService


class FakeSession:
    def __init__(self, store: list):
        self._store = store
        self.committed = False

    async def execute(self, sql, params):
        self._store.extend(params if isinstance(params, list) else [params])

    async def commit(self):
        self.committed = True


def _make_factory(store: list):
    @asynccontextmanager
    async def factory():
        s = FakeSession(store)
        yield s
    return factory


class TestRequestLogService:
    @pytest.mark.asyncio
    async def test_enqueue_and_flush(self):
        store: list = []
        svc = RequestLogService(_make_factory(store), batch_size=3, flush_interval_ms=50)
        await svc.start()
        try:
            for i in range(3):
                svc.enqueue(RequestLogEntry(
                    api_key_id=f"k{i}", status_code=200, latency_ms=10,
                ))
            await asyncio.sleep(0.2)
            assert len(store) == 3
            assert store[0]["api_key_id"] == "k0"
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_enqueue_non_blocking_on_full(self):
        store: list = []
        svc = RequestLogService(_make_factory(store), batch_size=50, flush_interval_ms=10000, max_queue=2)
        # start 하지 않아서 큐가 드레인 되지 않음
        svc.enqueue(RequestLogEntry(status_code=200, latency_ms=1))
        svc.enqueue(RequestLogEntry(status_code=200, latency_ms=1))
        # 이 호출은 queue full → 예외 없이 drop 되어야 함
        svc.enqueue(RequestLogEntry(status_code=200, latency_ms=1))
        # 예외 없음 확인 (여기 도달하면 통과)

    @pytest.mark.asyncio
    async def test_stop_drains_queue(self):
        store: list = []
        svc = RequestLogService(_make_factory(store), batch_size=100, flush_interval_ms=5000)
        await svc.start()
        for i in range(5):
            svc.enqueue(RequestLogEntry(api_key_id=f"k{i}", status_code=200, latency_ms=1))
        await svc.stop()
        assert len(store) == 5
