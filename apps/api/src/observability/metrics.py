"""Observability 메트릭: 노드별 레이턴시, 성공/실패율."""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class NodeMetrics:
    """노드별 메트릭 수집기."""

    call_count: int = 0
    total_ms: float = 0.0
    error_count: int = 0
    _durations: list[float] = field(default_factory=list)
    _max_samples: int = 1000
    _error_types: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    def add_duration(self, duration_ms: float) -> None:
        """레이턴시 샘플을 링 버퍼에 추가."""
        self._durations.append(duration_ms)
        if len(self._durations) >= self._max_samples:
            self._durations.pop(0)

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.call_count if self.call_count > 0 else 0.0

    @property
    def error_rate(self) -> float:
        return self.error_count / self.call_count if self.call_count > 0 else 0.0

    @property
    def p95_ms(self) -> float:
        """95퍼센타일 레이턴시."""
        if not self._durations:
            return 0.0
        sorted_durations = sorted(self._durations)
        idx = int(len(sorted_durations) * 0.95)
        return sorted_durations[min(idx, len(sorted_durations) - 1)]

    @property
    def p99_ms(self) -> float:
        """99퍼센타일 레이턴시."""
        if not self._durations:
            return 0.0
        sorted_durations = sorted(self._durations)
        idx = int(len(sorted_durations) * 0.99)
        return sorted_durations[min(idx, len(sorted_durations) - 1)]


class MetricsCollector:
    """전역 메트릭 수집기."""

    def __init__(self):
        self._metrics: dict[str, NodeMetrics] = defaultdict(NodeMetrics)

    def record(self, node: str, duration_ms: float, error: bool = False) -> None:
        m = self._metrics[node]
        m.call_count += 1
        m.total_ms += duration_ms
        m.add_duration(duration_ms)
        if error:
            m.error_count += 1

    def record_provider(
        self,
        provider: str,
        duration_ms: float,
        error: bool = False,
        error_type: Optional[str] = None
    ) -> None:
        """Provider별 메트릭 기록 (에러 타입 추적 포함)."""
        node_key = f"provider:{provider}"
        m = self._metrics[node_key]
        m.call_count += 1
        m.total_ms += duration_ms
        m.add_duration(duration_ms)

        if error:
            m.error_count += 1
            if error_type:
                m._error_types[provider][error_type] += 1

    def summary(self) -> dict:
        return {
            node: {
                "calls": m.call_count,
                "avg_ms": round(m.avg_ms, 1),
                "p95_ms": round(m.p95_ms, 1),
                "p99_ms": round(m.p99_ms, 1),
                "error_rate": round(m.error_rate, 3),
                "error_types": dict(m._error_types) if hasattr(m, '_error_types') and m._error_types else {},
            }
            for node, m in self._metrics.items()
        }

    def reset(self) -> None:
        self._metrics.clear()
