"""Observability 메트릭: 노드별 레이턴시, 성공/실패율."""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class NodeMetrics:
    """노드별 메트릭 수집기."""

    call_count: int = 0
    total_ms: float = 0.0
    error_count: int = 0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.call_count if self.call_count > 0 else 0.0

    @property
    def error_rate(self) -> float:
        return self.error_count / self.call_count if self.call_count > 0 else 0.0


class MetricsCollector:
    """전역 메트릭 수집기."""

    def __init__(self):
        self._metrics: dict[str, NodeMetrics] = defaultdict(NodeMetrics)

    def record(self, node: str, duration_ms: float, error: bool = False) -> None:
        m = self._metrics[node]
        m.call_count += 1
        m.total_ms += duration_ms
        if error:
            m.error_count += 1

    def summary(self) -> dict:
        return {
            node: {
                "calls": m.call_count,
                "avg_ms": round(m.avg_ms, 1),
                "error_rate": round(m.error_rate, 3),
            }
            for node, m in self._metrics.items()
        }

    def reset(self) -> None:
        self._metrics.clear()
