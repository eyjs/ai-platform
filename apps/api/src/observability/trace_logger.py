"""Trace Logger: 요청 단위 추적 + 레이어별 성능 기록.

RequestTrace가 Gateway에서 생성되어 Router → Agent → Tool → Safety를
거치며 각 노드의 결정과 레이턴시를 기록한다.

최종적으로 SSE done 이벤트 또는 응답 body에 trace 정보로 반환된다.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from src.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TraceNode:
    """추적 노드 — 하나의 처리 단계."""

    node: str
    duration_ms: float = 0.0
    data: dict = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)

    def finish(self, **extra: Any) -> None:
        self.duration_ms = (time.time() - self.start_time) * 1000
        self.data.update(extra)


@dataclass
class RequestTrace:
    """요청 전체 추적 — Gateway에서 생성, 레이어별로 노드 추가."""

    request_id: str
    nodes: list[TraceNode] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def start_node(self, name: str) -> TraceNode:
        """새 노드 시작. finish()로 종료."""
        node = TraceNode(node=name)
        self.nodes.append(node)
        return node

    def add_node(self, name: str, duration_ms: float, **data: Any) -> None:
        """이미 완료된 노드를 직접 추가."""
        self.nodes.append(TraceNode(
            node=name, duration_ms=duration_ms, data=data,
        ))

    @property
    def total_ms(self) -> float:
        return (time.time() - self.start_time) * 1000

    def summary(self) -> dict:
        """클라이언트에 반환할 trace 요약."""
        return {
            "request_id": self.request_id,
            "total_ms": round(self.total_ms, 1),
            "nodes": [
                {
                    "node": n.node,
                    "ms": round(n.duration_ms, 1),
                    **n.data,
                }
                for n in self.nodes
            ],
        }

    def log_summary(self) -> None:
        """전체 요청 처리 완료 후 구조화 로그로 요약 출력."""
        node_summary = " → ".join(
            f"{n.node}({n.duration_ms:.0f}ms)" for n in self.nodes
        )
        logger.info(
            "request_complete",
            request_id=self.request_id,
            total_ms=round(self.total_ms, 1),
            node_count=len(self.nodes),
            pipeline=node_summary,
        )
