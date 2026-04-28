"""MetricsCollector 고도화 테스트.

p95/p99 정확도, record_provider(), 링 버퍼 제한, summary() 포맷.
"""

from __future__ import annotations

from src.observability.metrics import MetricsCollector, NodeMetrics


class TestNodeMetrics:
    def test_empty_metrics(self):
        m = NodeMetrics()
        assert m.avg_ms == 0.0
        assert m.error_rate == 0.0
        assert m.p95_ms == 0.0
        assert m.p99_ms == 0.0

    def test_p95_p99_known_distribution(self):
        m = NodeMetrics()
        for i in range(1, 101):
            m.call_count += 1
            m.total_ms += float(i)
            m.add_duration(float(i))

        # int(100 * 0.95) = 95 → sorted[95] = 96.0
        assert m.p95_ms == 96.0
        # int(100 * 0.99) = 99 → sorted[99] = 100.0
        assert m.p99_ms == 100.0
        assert m.avg_ms == 50.5

    def test_ring_buffer_limit(self):
        m = NodeMetrics()
        for i in range(1200):
            m.add_duration(float(i))

        assert len(m._durations) < 1000

    def test_error_rate(self):
        m = NodeMetrics()
        m.call_count = 10
        m.error_count = 3
        assert abs(m.error_rate - 0.3) < 1e-9


class TestMetricsCollector:
    def test_record_basic(self):
        mc = MetricsCollector()
        mc.record("node_a", 100.0)
        mc.record("node_a", 200.0, error=True)

        s = mc.summary()
        assert "node_a" in s
        assert s["node_a"]["calls"] == 2
        assert s["node_a"]["error_rate"] == 0.5

    def test_record_provider(self):
        mc = MetricsCollector()
        mc.record_provider("openai", 150.0)
        mc.record_provider("openai", 300.0, error=True, error_type="RateLimitError")
        mc.record_provider("openai", 200.0, error=True, error_type="RateLimitError")
        mc.record_provider("openai", 50.0, error=True, error_type="Timeout")

        s = mc.summary()
        key = "provider:openai"
        assert key in s
        assert s[key]["calls"] == 4
        assert s[key]["error_rate"] == 0.75
        assert s[key]["error_types"]["openai"]["RateLimitError"] == 2
        assert s[key]["error_types"]["openai"]["Timeout"] == 1

    def test_record_provider_no_error_type(self):
        mc = MetricsCollector()
        mc.record_provider("anthropic", 100.0, error=True)

        s = mc.summary()
        key = "provider:anthropic"
        assert s[key]["error_rate"] > 0
        assert s[key]["error_types"] == {}

    def test_summary_format(self):
        mc = MetricsCollector()
        mc.record("intent_classifier", 50.0)
        mc.record("intent_classifier", 60.0)

        s = mc.summary()
        entry = s["intent_classifier"]
        assert set(entry.keys()) == {"calls", "avg_ms", "p95_ms", "p99_ms", "error_rate", "error_types"}
        assert isinstance(entry["avg_ms"], float)

    def test_reset(self):
        mc = MetricsCollector()
        mc.record("node_x", 100.0)
        mc.record_provider("ollama", 200.0)
        mc.reset()
        assert mc.summary() == {}

    def test_multiple_nodes_isolated(self):
        mc = MetricsCollector()
        mc.record("node_a", 100.0, error=True)
        mc.record("node_b", 200.0)

        s = mc.summary()
        assert s["node_a"]["error_rate"] == 1.0
        assert s["node_b"]["error_rate"] == 0.0

    def test_p95_p99_via_collector(self):
        mc = MetricsCollector()
        for i in range(1, 201):
            mc.record("latency_node", float(i))

        s = mc.summary()
        # int(200 * 0.95) = 190 → sorted[190] = 191.0
        assert s["latency_node"]["p95_ms"] == 191.0
        # int(200 * 0.99) = 198 → sorted[198] = 199.0
        assert s["latency_node"]["p99_ms"] == 199.0
