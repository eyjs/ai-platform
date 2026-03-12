"""Trace Logger: 라우팅 결정 + Tool 실행 로깅."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TraceEntry:
    """추적 항목."""

    node: str
    duration_ms: float
    data: dict = field(default_factory=dict)


@dataclass
class RequestTrace:
    """요청 전체 추적."""

    request_id: str
    entries: list[TraceEntry] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def add(self, node: str, duration_ms: float, **data: Any) -> None:
        self.entries.append(TraceEntry(node=node, duration_ms=duration_ms, data=data))

    @property
    def total_ms(self) -> float:
        return (time.time() - self.start_time) * 1000

    def summary(self) -> dict:
        return {
            "request_id": self.request_id,
            "total_ms": self.total_ms,
            "nodes": [
                {"node": e.node, "ms": e.duration_ms, **e.data}
                for e in self.entries
            ],
        }
