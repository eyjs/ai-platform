"""Gateway 통합 헬퍼 테스트 (Task 009)."""

from __future__ import annotations

import asyncio
import pytest

from src.gateway.gateway_hooks import (
    latency_timer,
    safe_enqueue,
    should_use_cache,
    try_cache_get,
    try_cache_put,
)
from src.observability.request_log_models import RequestLogEntry


class TestLatencyTimer:
    def test_measures_time(self):
        import time
        with latency_timer() as t:
            time.sleep(0.05)
        assert t["elapsed_ms"] >= 40


class TestSafeEnqueue:
    def test_none_svc_no_error(self):
        safe_enqueue(None, RequestLogEntry(status_code=200, latency_ms=1))

    def test_broken_svc_no_error(self):
        class BrokenSvc:
            def enqueue(self, e):
                raise RuntimeError("boom")
        # 절대 전파되지 않음
        safe_enqueue(BrokenSvc(), RequestLogEntry(status_code=200, latency_ms=1))


class TestCacheHooks:
    @pytest.mark.asyncio
    async def test_try_cache_get_none_returns_none(self):
        out = await try_cache_get(None, "p1", "deterministic", "hi")
        assert out is None

    @pytest.mark.asyncio
    async def test_try_cache_put_none_noop(self):
        await try_cache_put(None, "p1", "deterministic", "hi", "resp")

    def test_should_use_cache_no_service(self):
        assert should_use_cache(object(), "deterministic", None) is False
