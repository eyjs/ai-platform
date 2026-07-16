"""FailoverLLMProvider — primary(DGX Spark 등 원격) 실패 시 fallback(로컬)으로 자동 전환.

사용자 요구: "DGX Spark 연결이 끊어지면 지금 현재 구조로 폴백하는 구조".

동작:
- 매 호출 primary 우선. 연결류 실패(타임아웃·거부·서킷 오픈·5xx)면 fallback으로
  같은 호출을 재수행하고, 쿨다운 동안 primary를 건너뛴다(다운된 원격에 매 요청
  connect 타임아웃을 물지 않게). 쿨다운이 지나면 다음 호출이 primary를 재시도
  — 복구 시 자동 복귀.
- 스트리밍은 첫 청크 이전 실패만 폴백(부분 출력 후 재시작은 중복 노출 위험).
"""

import time
from typing import AsyncIterator, Optional

import httpx

from ..base import LLMProvider, ProviderCapability, StreamChunk
from .._resilience import CircuitOpenError
from src.observability.logging import get_logger

logger = get_logger(__name__)

# primary 실패 후 이 시간 동안은 fallback 직행 (다운 원격에 반복 대기 방지)
PRIMARY_COOLDOWN_SECONDS = 30.0

# 폴백을 유발하는 실패 부류 — 연결/가용성 문제만. (프롬프트 오류 등 4xx 논리
# 오류는 폴백해도 같은 결과라 전파한다.)
_FAILOVER_ERRORS = (
    httpx.TransportError,   # 연결 거부/타임아웃/DNS 등
    CircuitOpenError,
    TimeoutError,
    ConnectionError,
)


def _is_failover_status(exc: Exception) -> bool:
    """HTTP 상태 오류 중 폴백 대상(서버측 5xx)인지."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


class FailoverLLMProvider(LLMProvider):
    """primary → fallback 자동 전환 래퍼."""

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider,
        label: str = "llm",
        cooldown_seconds: float = PRIMARY_COOLDOWN_SECONDS,
    ):
        self._primary = primary
        self._fallback = fallback
        self._label = label
        self._cooldown = cooldown_seconds
        self._primary_failed_at: float = 0.0

    # --- 상태 ---

    @property
    def _primary_available(self) -> bool:
        return (time.monotonic() - self._primary_failed_at) >= self._cooldown

    def _mark_primary_failed(self, exc: Exception) -> None:
        self._primary_failed_at = time.monotonic()
        logger.warning(
            "llm_failover",
            label=self._label,
            error=f"{type(exc).__name__}: {exc}",
            cooldown_seconds=self._cooldown,
        )

    def _mark_primary_ok(self) -> None:
        """primary 성공. 폴백 중이었다면 복귀를 남긴다.

        복구 로그가 없으면 "언제 폴백에서 빠져나왔는지"를 알 수 없어, 저품질(폴백 모델)
        구간의 끝을 특정하지 못한다. 전이일 때만 남긴다 — 정상 호출은 무소음.
        """
        if self._primary_failed_at:
            self._primary_failed_at = 0.0
            logger.info("llm_failover_recovered", label=self._label)

    def _should_failover(self, exc: Exception) -> bool:
        return isinstance(exc, _FAILOVER_ERRORS) or _is_failover_status(exc)

    @property
    def capability(self) -> ProviderCapability:
        return self._primary.capability

    async def is_available(self) -> bool:
        return await self._primary.is_available() or await self._fallback.is_available()

    # --- 위임 ---

    async def generate(self, prompt: str, system: str = "", **kwargs) -> str:
        if self._primary_available:
            try:
                result = await self._primary.generate(prompt, system=system, **kwargs)
                self._mark_primary_ok()
                return result
            except Exception as e:  # noqa: BLE001 - 폴백 대상만 전환, 나머지 전파
                if not self._should_failover(e):
                    raise
                self._mark_primary_failed(e)
        return await self._fallback.generate(prompt, system=system, **kwargs)

    async def generate_json(self, prompt: str, system: str = "", **kwargs) -> dict:
        if self._primary_available:
            try:
                result = await self._primary.generate_json(prompt, system=system, **kwargs)
                self._mark_primary_ok()
                return result
            except Exception as e:  # noqa: BLE001
                if not self._should_failover(e):
                    raise
                self._mark_primary_failed(e)
        return await self._fallback.generate_json(prompt, system=system, **kwargs)

    async def generate_stream(
        self, prompt: str, system: str = "", **kwargs,
    ) -> AsyncIterator[str]:
        async for chunk in self.generate_stream_typed(prompt, system=system, **kwargs):
            if chunk.kind == "answer":
                yield chunk.content

    async def generate_stream_typed(
        self, prompt: str, system: str = "", **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        if self._primary_available:
            emitted = False
            try:
                async for chunk in self._primary.generate_stream_typed(
                    prompt, system=system, **kwargs,
                ):
                    emitted = True
                    yield chunk
                self._mark_primary_ok()
                return
            except Exception as e:  # noqa: BLE001
                # 첫 청크 이전의 가용성 실패만 폴백 — 부분 출력 후엔 전파(중복 방지)
                if emitted or not self._should_failover(e):
                    raise
                self._mark_primary_failed(e)
        async for chunk in self._fallback.generate_stream_typed(
            prompt, system=system, **kwargs,
        ):
            yield chunk
