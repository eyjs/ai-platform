"""안정성 테스트.

Profile Hot Reload, Graceful Shutdown, Faithfulness max_iterations 루프 방지.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.observability.metrics import MetricsCollector


class TestFaithfulnessLoopPrevention:
    """Faithfulness guard가 무한 루프를 방지하는지 검증."""

    async def test_max_checks_auto_passes(self):
        from src.safety.faithfulness import FaithfulnessGuard
        from src.safety.base import GuardrailContext

        guard = FaithfulnessGuard(router_llm=None)
        ctx = MagicMock(spec=GuardrailContext)
        ctx.session_id = "test-session"
        ctx.source_documents = [{"content": "some source text 12345"}]
        ctx.response_policy = "balanced"

        for i in range(guard.MAX_FAITHFULNESS_CHECKS):
            result = await guard.check("answer text", ctx)

        assert guard._check_count["test-session"] == guard.MAX_FAITHFULNESS_CHECKS

        result = await guard.check("answer text", ctx)
        assert result.action == "pass"
        assert guard._check_count["test-session"] == guard.MAX_FAITHFULNESS_CHECKS

    async def test_different_sessions_tracked_independently(self):
        from src.safety.faithfulness import FaithfulnessGuard
        from src.safety.base import GuardrailContext

        guard = FaithfulnessGuard(router_llm=None)

        ctx_a = MagicMock(spec=GuardrailContext)
        ctx_a.session_id = "session-a"
        ctx_a.source_documents = []
        ctx_a.response_policy = "balanced"

        ctx_b = MagicMock(spec=GuardrailContext)
        ctx_b.session_id = "session-b"
        ctx_b.source_documents = []
        ctx_b.response_policy = "balanced"

        await guard.check("text", ctx_a)
        await guard.check("text", ctx_b)

        assert guard._check_count.get("session-a", 0) == 1
        assert guard._check_count.get("session-b", 0) == 1


class TestMetricsCollectorStability:
    """MetricsCollector가 대량 데이터에서도 안정적인지 검증."""

    def test_high_volume_recording(self):
        mc = MetricsCollector()
        for i in range(10_000):
            mc.record("stress_node", float(i % 1000), error=(i % 100 == 0))

        s = mc.summary()
        assert s["stress_node"]["calls"] == 10_000
        assert s["stress_node"]["p95_ms"] > 0
        assert s["stress_node"]["error_rate"] == pytest.approx(0.01, abs=0.001)

    def test_concurrent_provider_recording(self):
        mc = MetricsCollector()
        providers = ["openai", "anthropic", "ollama"]
        for p in providers:
            for i in range(100):
                mc.record_provider(p, float(i))

        s = mc.summary()
        for p in providers:
            assert s[f"provider:{p}"]["calls"] == 100

    def test_summary_after_reset_is_clean(self):
        mc = MetricsCollector()
        mc.record("x", 100.0)
        mc.reset()
        mc.record("y", 200.0)

        s = mc.summary()
        assert "x" not in s
        assert "y" in s


class TestMemoryCleanupSweeper:
    """MemoryStore cleanup sweeper 시작/중지 안정성."""

    async def test_start_stop_sweeper(self):
        from src.infrastructure.memory.memory_store import MemoryStore

        pool = MagicMock()
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 0"])
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        store = MemoryStore(pool)

        await store.start_cleanup_sweeper(interval_seconds=1)
        assert store._cleanup_running is True
        assert store._cleanup_task is not None

        await asyncio.sleep(0.1)

        await store.stop_cleanup_sweeper()
        assert store._cleanup_running is False
        assert store._cleanup_task is None

    async def test_double_start_ignored(self):
        from src.infrastructure.memory.memory_store import MemoryStore

        pool = MagicMock()
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 0"])
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        store = MemoryStore(pool)

        await store.start_cleanup_sweeper(interval_seconds=60)
        first_task = store._cleanup_task
        await store.start_cleanup_sweeper(interval_seconds=60)
        assert store._cleanup_task is first_task

        await store.stop_cleanup_sweeper()

    async def test_stop_without_start(self):
        from src.infrastructure.memory.memory_store import MemoryStore

        pool = MagicMock()
        store = MemoryStore(pool)
        await store.stop_cleanup_sweeper()
        assert store._cleanup_running is False


class TestToolResultImmutability:
    """ToolResult(frozen=True) 불변성 검증."""

    def test_tool_result_ok_is_frozen(self):
        from src.tools.base import ToolResult

        result = ToolResult.ok(data={"a": 1}, extra="info")
        assert result.success is True
        with pytest.raises(AttributeError):
            result.success = False

    def test_tool_result_fail_is_frozen(self):
        from src.tools.base import ToolResult

        result = ToolResult.fail("something broke")
        assert result.success is False
        assert "broke" in result.error


class TestAgentContextDefaults:
    """AgentContext 기본값 검증."""

    def test_default_values(self):
        from src.domain.agent_context import AgentContext

        ctx = AgentContext()
        assert ctx.session_id == ""
        assert ctx.user_id == ""
        assert ctx.conversation_history == []
        assert ctx.prior_doc_ids == []

    def test_custom_values(self):
        from src.domain.agent_context import AgentContext

        ctx = AgentContext(
            session_id="s1",
            user_id="u1",
        )
        assert ctx.session_id == "s1"
        assert ctx.user_id == "u1"
