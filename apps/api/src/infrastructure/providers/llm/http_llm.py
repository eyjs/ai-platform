"""HTTP LLM 프로바이더 (GPU 서버 / MLX 서버용).

Qwen3 등 thinking 모델의 <think>...</think> 블록을
답변과 분리하여 별도 스트림 이벤트로 전달한다.
"""

import asyncio
import json
import logging
import re
from typing import AsyncIterator

import httpx

from ..base import LLMProvider, ProviderCapability, StreamChunk
from .._resilience import CircuitBreaker, CircuitOpenError, is_transient, retry_async

logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>\s*", re.DOTALL)


class HttpLLMProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        system_prefix: str = "",
        max_tokens: int = 4096,
        retry_attempts: int = 2,
        circuit_fail_threshold: int = 5,
        circuit_cooldown_seconds: float = 15.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)
        self._system_prefix = system_prefix
        self._max_tokens = max_tokens
        self._retry_attempts = retry_attempts
        # 서버 다운 시 매 요청이 120초 타임아웃까지 hang 하는 것을 막는다(로그 617류 지연).
        self._breaker = CircuitBreaker(
            fail_threshold=circuit_fail_threshold,
            cooldown_seconds=circuit_cooldown_seconds,
            name=f"llm:{self._base_url}",
        )

    async def _post_completion(
        self, system_msg: str, prompt: str, temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """비스트리밍 chat completion 1회 호출 — 재시도/서킷은 호출부가 감싼다.

        temperature=None 이면 서버 기본값(샘플링). 구조적 판단(JSON)은 0.0(그리디)을
        명시해 비결정성을 제거한다 — decompose 오라우팅이 호출마다 튀던 실사고 대응.
        """
        payload = {
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "max_tokens": max_tokens or self._max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        response = await self._client.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    @property
    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider_id="http_llm",
            supports_tool_use=False,
            supports_streaming=True,
            max_context=8192,
            cost_per_1k_tokens=0.0,
            stub=False,
        )

    async def is_available(self) -> bool:
        try:
            r = await self._client.get(f"{self._base_url}/health", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def split_thinking(text: str) -> tuple[str, str]:
        """완성된 텍스트에서 thinking/answer를 분리한다.

        Returns:
            (thinking, answer) 튜플. thinking이 없으면 빈 문자열.
        """
        match = _THINK_BLOCK_RE.search(text)
        if match:
            thinking = match.group(1).strip()
            answer = _THINK_BLOCK_RE.sub("", text).strip()
            return thinking, answer
        return "", text.strip()

    async def generate(
        self, prompt: str, system: str = "",
        cacheable_system: str = "", volatile_system: str = "",
        max_tokens: int | None = None,
    ) -> str:
        """답변만 반환한다 (thinking 제거). cacheable/volatile은 결합(캐싱 미지원)."""
        system_msg = self._build_system(
            self._combine_system(system, cacheable_system, volatile_system)
        )
        text = await retry_async(
            lambda: self._post_completion(system_msg, prompt, max_tokens=max_tokens),
            attempts=self._retry_attempts,
            breaker=self._breaker,
            name="llm",
        )
        _, answer = self.split_thinking(text)
        return answer

    async def generate_with_thinking(self, prompt: str, system: str = "") -> tuple[str, str]:
        """thinking과 answer를 분리하여 반환한다.

        Returns:
            (thinking, answer) 튜플.
        """
        system_msg = self._build_system(system)
        text = await retry_async(
            lambda: self._post_completion(system_msg, prompt),
            attempts=self._retry_attempts,
            breaker=self._breaker,
            name="llm",
        )
        return self.split_thinking(text)

    async def generate_json(
        self, prompt: str, system: str = "",
        cacheable_system: str = "", volatile_system: str = "",
    ) -> dict:
        # 구조적 판단(라우팅/계획/판정)은 그리디(temperature=0) — 같은 입력 = 같은 결정.
        system_msg = self._build_system(
            self._combine_system(system, cacheable_system, volatile_system)
        )
        text = await retry_async(
            lambda: self._post_completion(system_msg, prompt, temperature=0.0),
            attempts=self._retry_attempts,
            breaker=self._breaker,
            name="llm",
        )
        _, text = self.split_thinking(text)
        # LLM이 ```json ... ``` 으로 감싸는 경우 fence 제거
        stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
        stripped = re.sub(r"\n?```\s*$", "", stripped)
        return json.loads(stripped)

    async def generate_stream(
        self, prompt: str, system: str = "",
        cacheable_system: str = "", volatile_system: str = "",
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """하위 호환: 답변 텍스트만 yield (thinking 제거)."""
        async for chunk in self.generate_stream_typed(
            prompt, system, cacheable_system=cacheable_system, volatile_system=volatile_system,
            max_tokens=max_tokens,
        ):
            if chunk.kind == "answer":
                yield chunk.content

    async def generate_stream_typed(
        self, prompt: str, system: str = "",
        cacheable_system: str = "", volatile_system: str = "",
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """thinking/answer를 StreamChunk로 구분하여 yield.

        회복력: 서킷이 열려 있으면 즉시 fast-fail. 첫 청크 방출 이전의 일시적
        실패만 재시도한다(부분 출력 후 재시도는 중복 위험이라 금지). 첫 청크가
        나온 뒤의 실패는 그대로 전파한다.
        """
        system_msg = self._build_system(
            self._combine_system(system, cacheable_system, volatile_system)
        )
        if self._breaker.is_open:
            raise CircuitOpenError(f"llm circuit open: {self._base_url}")

        attempt = 0
        while True:
            attempt += 1
            emitted = False
            try:
                async for chunk in self._stream_once(system_msg, prompt, max_tokens=max_tokens):
                    emitted = True
                    yield chunk
                self._breaker.record_success()
                return
            except Exception as e:
                # 첫 청크 이전의 일시적 오류만 재시도(중복 방출 방지).
                if not emitted and is_transient(e) and attempt < self._retry_attempts:
                    await asyncio.sleep(0.2 * (2 ** (attempt - 1)))
                    continue
                self._breaker.record_failure()
                raise

    async def _stream_once(
        self, system_msg: str, prompt: str, max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """스트리밍 chat completion 1회 — 파싱 로직. 재시도/서킷은 호출부가 감싼다."""
        in_thinking = False

        async with self._client.stream(
            "POST",
            f"{self._base_url}/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "stream": True,
                "max_tokens": max_tokens or self._max_tokens,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                data = json.loads(data_str)
                delta = data.get("choices", [{}])[0].get("delta", {})
                if not (content := delta.get("content")):
                    continue

                # <think> 태그 기반 상태 전환
                if "<think>" in content:
                    in_thinking = True
                    # <think> 이전 텍스트가 있으면 answer로 전달
                    before = content.split("<think>")[0]
                    if before:
                        yield StreamChunk(kind="answer", content=before)
                    # <think> 이후 텍스트가 있으면 thinking으로 전달
                    after = content.split("<think>", 1)[1]
                    if "</think>" in after:
                        think_text = after.split("</think>")[0]
                        if think_text:
                            yield StreamChunk(kind="thinking", content=think_text)
                        in_thinking = False
                        remainder = after.split("</think>", 1)[1]
                        if remainder:
                            yield StreamChunk(kind="answer", content=remainder)
                    elif after:
                        yield StreamChunk(kind="thinking", content=after)
                    continue

                if "</think>" in content:
                    in_thinking = False
                    # </think> 이전 텍스트는 thinking
                    before = content.split("</think>")[0]
                    if before:
                        yield StreamChunk(kind="thinking", content=before)
                    # </think> 이후 텍스트는 answer
                    after = content.split("</think>", 1)[1]
                    if after:
                        yield StreamChunk(kind="answer", content=after)
                    continue

                yield StreamChunk(
                    kind="thinking" if in_thinking else "answer",
                    content=content,
                )
