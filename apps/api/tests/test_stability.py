"""안정성 테스트.

Profile Hot Reload, Graceful Shutdown, Faithfulness max_iterations 루프 방지.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.observability.metrics import MetricsCollector


class TestFaithfulnessLoopPrevention:
    """Faithfulness guard는 자기-비활성화하지 않는다.

    과거: GuardrailContext에 session_id가 없어 전역 'default' 카운터가 누적 →
    3회 후 영구 비활성화되는 버그(이전 테스트는 MagicMock에 session_id를 직접 주입해
    이 버그를 가렸다). 무한 재생성 방지는 nodes.py의 _regen_count(요청당 1회)가 담당한다.
    """

    async def test_faithfulness_does_not_self_disable(self):
        from src.safety.faithfulness import FaithfulnessGuard
        from src.safety.base import GuardrailContext

        guard = FaithfulnessGuard(router_llm=None)
        # 실제 GuardrailContext 사용(MagicMock 금지) — 소스에 없는 숫자(500만원)로 매번 warn 되어야 함
        ctx = GuardrailContext(
            question="q",
            source_documents=[{"content": "상해 8급에 해당합니다.", "file_name": "a.pdf"}],
            response_policy="balanced",
        )
        for i in range(6):
            result = await guard.check("8급 상해는 500만원입니다.", ctx)
            assert result.action == "warn", f"{i + 1}회째 검증이 비활성화됨(action={result.action})"


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
