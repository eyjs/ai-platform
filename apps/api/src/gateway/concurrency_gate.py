"""전역 동시 실행 게이트 — 에이전트 실행 수를 프로세스 단위로 유계화한다.

배경(아키텍처 진단 2026-07-15): `max_concurrent_agents`가 Settings에 있는데 소비처가
없었다. 설정만 보면 "50명까지 받는구나" 싶지만 실제로는 상한이 없어, 부하가 오면
에이전트가 무제한으로 쌓이고 PG 풀·LLM 큐가 먼저 무너졌다. 진단이 "용량 절벽붕괴"라
부른 지점이며, 설정이 그 사실을 가리고 있었다(죽은 설정).

**대기가 아니라 즉시 거부**다. 상한을 넘겼을 때 큐잉하면 이미 늦은 요청이 타임아웃까지
슬롯을 물고 있어 절벽붕괴를 늦출 뿐 막지 못한다. 빨리 거절해야 클라이언트가 재시도를
결정할 수 있다(Retry-After).

asyncio.Semaphore 대신 정수 카운터를 쓰는 이유: Semaphore는 "기다린다"가 기본이고
비대기 획득 API가 없다(_value는 사설). 이 게이트는 대기하지 않으므로 카운터가 곧
의미다. asyncio 단일 스레드에서 try_acquire 내부에 await가 없어 원자적이다.

**프로세스 단위**임에 주의: uvicorn workers를 늘리면 상한이 워커 수만큼 곱해진다
(진단 P2 "프로세스별 상태 곱셈"). 멀티 워커로 갈 때는 이 상한을 워커 수로 나누거나
공유 저장소(PG) 기반으로 바꿔야 한다.
"""

from __future__ import annotations

from src.observability.logging import get_logger

logger = get_logger(__name__)

# 거부 시 클라이언트에 제시할 재시도 대기(초). 동시성은 레이트리밋과 달리 "충전 속도"가
# 없어 정확한 계산이 불가능하다 — 한 요청이 빠지는 데 걸리는 시간(수 초)에 맞춘 어림값.
RETRY_AFTER_SECONDS = 5


class ConcurrencyGate:
    """동시 실행 슬롯. 상한 초과 시 즉시 거부한다.

    limit <= 0 이면 무제한 — 게이트를 끄는 탈출구(단일 사용자 개발 환경 등).
    """

    def __init__(self, limit: int):
        self._limit = limit
        self._active = 0
        self._rejected = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    @property
    def rejected(self) -> int:
        """누적 거부 수 — 상한이 실제로 물리는지 보는 관측점."""
        return self._rejected

    def try_acquire(self) -> bool:
        """슬롯을 잡는다. 실패하면 False (대기하지 않는다)."""
        if self._limit <= 0:
            return True
        if self._active >= self._limit:
            self._rejected += 1
            logger.warning(
                "concurrency_limit_exceeded",
                layer="GATEWAY",
                active=self._active,
                limit=self._limit,
                rejected_total=self._rejected,
                hint="AIP_MAX_CONCURRENT_AGENTS 상한 도달 — 503으로 즉시 거부",
            )
            return False
        self._active += 1
        return True

    def release(self) -> None:
        """슬롯을 놓는다. try_acquire가 True를 준 경우에만 호출할 것."""
        if self._limit <= 0:
            return
        self._active = max(0, self._active - 1)

    def snapshot(self) -> dict:
        """/api/health 노출용 — 상한이 얼마고 지금 얼마나 차 있는지."""
        return {
            "limit": self._limit,
            "active": self._active,
            "rejected_total": self._rejected,
        }
