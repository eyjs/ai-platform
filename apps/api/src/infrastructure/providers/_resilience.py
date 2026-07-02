"""HTTP provider 회복력 유틸: 지수 백오프 재시도 + 서킷 브레이커.

임베딩처럼 degrade 불가능한 필수 HTTP 의존점(MLX GPU 서버)을 견고화한다.
- 일시적 blip(연결 거부·타임아웃·5xx)은 재시도로 복구한다.
- 서버가 완전히 죽으면 서킷을 열어 fast-fail 하여, 매 요청이 타임아웃까지
  hang 하며 스택을 물고 늘어지는 것을 막는다(로그 617류 지연 방지).

외부 라이브러리 없이 asyncio/time 만 사용(PostgreSQL 단일 스택 원칙과 무관한
순수 in-process 상태). reranker/LLM HTTP provider 도 동일 유틸을 재사용 가능.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_transient(exc: Exception) -> bool:
    """재시도 가치가 있는 일시적 오류인지 판정.

    - 네트워크/타임아웃: 재시도 대상
    - 5xx: 서버 일시 오류 → 재시도 대상
    - 4xx: 요청 자체 오류 → 재시도 무의미(즉시 raise)
    """
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


class CircuitOpenError(RuntimeError):
    """서킷이 열린 상태에서 호출을 fast-fail 할 때 발생."""


class CircuitBreaker:
    """연속 실패가 임계치를 넘으면 열려서 fast-fail, cooldown 후 half-open(1회 탐침).

    상태 판정에 time.monotonic() 만 사용(벽시계 비의존).
    """

    def __init__(
        self,
        *,
        fail_threshold: int = 5,
        cooldown_seconds: float = 10.0,
        name: str = "http",
    ):
        self._fail_threshold = fail_threshold
        self._cooldown = cooldown_seconds
        self._name = name
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        # cooldown 경과 → half-open(호출 1회 허용). 실패 시 record_failure 가 재-arm.
        return (time.monotonic() - self._opened_at) < self._cooldown

    def record_success(self) -> None:
        if self._consecutive_failures or self._opened_at is not None:
            logger.info("circuit.close name=%s", self._name)
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._fail_threshold:
            # 임계치 도달/초과 시마다 cooldown 재-arm(half-open 탐침 실패 → 재개방).
            self._opened_at = time.monotonic()
            logger.warning(
                "circuit.open name=%s failures=%d cooldown=%.0fs",
                self._name,
                self._consecutive_failures,
                self._cooldown,
            )


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.2,
    breaker: CircuitBreaker | None = None,
    name: str = "http",
) -> T:
    """fn 을 재시도(지수 백오프)하며 실행. 선택적으로 서킷 브레이커와 연동.

    - 서킷이 열려 있으면 즉시 CircuitOpenError(호출부는 이를 실패로 처리).
    - 일시적 오류만 재시도, 비일시적(4xx 등)은 즉시 raise.
    - 모든 시도 소진 시 마지막 예외를 raise 하고 breaker 에 실패 1회 기록.
    """
    if breaker is not None and breaker.is_open:
        raise CircuitOpenError(f"{name} circuit open")

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = await fn()
            if breaker is not None:
                breaker.record_success()
            return result
        except Exception as e:  # noqa: BLE001 — transient 판정 후 재-raise
            last_exc = e
            if not is_transient(e):
                raise
            if attempt < attempts:
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)))

    if breaker is not None:
        breaker.record_failure()
    assert last_exc is not None
    raise last_exc
