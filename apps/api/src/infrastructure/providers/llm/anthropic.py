"""Anthropic Claude Provider.

- AnthropicLLMProvider: 실 SDK(anthropic.AsyncAnthropic) 구현. 프로덕션 메인/라우터 LLM.
- AnthropicStubProvider: SDK 미설치/개발용 스텁(capability.stub=True).

기본 모델은 비용 최적 claude-haiku-4-5. Prompt Caching(system prefix에
cache_control ephemeral)으로 반복 prefix 비용을 ~0.1x로 절감한다.
"""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

from ..base import LLMProvider, ProviderCapability, ProviderUnavailableError, StreamChunk

logger = logging.getLogger(__name__)

# 비용 최적 기본 모델 (input $1 / output $5 per 1M)
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"


class AnthropicLLMProvider(LLMProvider):
    """Anthropic Messages API 어댑터 (실 SDK).

    anthropic 패키지는 지연 import — 미설치 시 명확한 에러.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        system_prefix: str = "",
        max_tokens: int = 4096,
    ) -> None:
        if not api_key:
            raise ProviderUnavailableError(
                "anthropic_claude", "AIP_ANTHROPIC_API_KEY 미설정 (프로덕션 Anthropic 모드)"
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:  # 의존성 안내를 명확히
            raise ProviderUnavailableError(
                "anthropic_claude",
                'anthropic 패키지 미설치 — pip install -e ".[anthropic]"',
            ) from e

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._system_prefix = system_prefix
        self._max_tokens = max_tokens

    @property
    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider_id="anthropic_claude",
            supports_tool_use=True,
            supports_streaming=True,
            max_context=200000,
            cost_per_1k_tokens=0.001,  # haiku input 기준
            stub=False,
            supports_prompt_caching=True,
        )

    def _system_kwarg(self, system: str = "") -> dict:
        """messages.create 의 system 인자를 구성한다(Prompt Caching).

        정적 system_prefix는 cache_control ephemeral 블록으로 캐시 가능하게,
        동적 system은 그 뒤에 일반 블록으로 붙인다. 비면 인자 자체를 생략.
        """
        blocks: list[dict] = []
        if self._system_prefix:
            blocks.append({
                "type": "text",
                "text": self._system_prefix,
                "cache_control": {"type": "ephemeral"},
            })
        if system:
            blocks.append({"type": "text", "text": system})
        return {"system": blocks} if blocks else {}

    def _log_cache_usage(self, usage) -> None:
        if not usage:
            return
        cached = getattr(usage, "cache_read_input_tokens", 0) or 0
        if cached > 0:
            total = getattr(usage, "input_tokens", 0) or 0
            logger.debug(
                "anthropic_prompt_cache_hit",
                extra={"cache_read_tokens": cached, "input_tokens": total},
            )

    async def generate(self, prompt: str, system: str = "") -> str:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **self._system_kwarg(system),
        )
        self._log_cache_usage(getattr(resp, "usage", None))
        return "".join(b.text for b in resp.content if b.type == "text")

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        # 스키마 비의존 generate_json: JSON-only 지시 후 파싱(모델 무관 견고).
        json_system = "You must respond with a single valid JSON object only — no prose, no code fences."
        merged = f"{system}\n\n{json_system}" if system else json_system
        text = (await self.generate(prompt, system=merged)).strip()
        if text.startswith("```"):
            # ```json ... ``` 펜스 제거
            inner = text.split("```")
            text = inner[1].lstrip("json").strip() if len(inner) >= 2 else text
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ProviderUnavailableError(
                "anthropic_claude", f"JSON 파싱 실패: {e} / preview={text[:200]}"
            ) from e

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **self._system_kwarg(system),
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            self._log_cache_usage(getattr(final, "usage", None))

    async def generate_stream_typed(self, prompt: str, system: str = "") -> AsyncIterator[StreamChunk]:
        async for token in self.generate_stream(prompt, system):
            yield StreamChunk(kind="answer", content=token)


class AnthropicStubProvider(LLMProvider):
    """Anthropic Claude 어댑터 스텁.

    - SDK import 금지.
    - 개발 단계: deterministic placeholder 응답 (`AIP_PROVIDER_ANTHROPIC_STUB_MODE=echo`)
    - 프로덕션 전환 시: `_real_client` 를 실 SDK 로 교체.
    """

    def __init__(self, system_prefix: str = "", max_tokens: int = 4096) -> None:
        self._system_prefix = system_prefix
        self._max_tokens = max_tokens
        self._stub_mode = os.getenv("AIP_PROVIDER_ANTHROPIC_STUB_MODE", "raise")

    @property
    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider_id="anthropic_claude",
            supports_tool_use=True,
            supports_streaming=True,
            max_context=200000,
            cost_per_1k_tokens=0.008,
            stub=True,
            supports_prompt_caching=True,
        )

    async def is_available(self) -> bool:
        # stub 은 항상 True (등록은 가능, 호출 시 분기)
        return True

    def _build_system_blocks(self, system: str = "") -> list[dict]:
        """Anthropic Prompt Caching용 system 블록 구성.

        프로덕션 전환 시 cache_control: {"type": "ephemeral"} 블록으로
        system_prefix를 캐시 가능하게 전달한다.
        """
        blocks = []
        if self._system_prefix:
            blocks.append({
                "type": "text",
                "text": self._system_prefix,
                "cache_control": {"type": "ephemeral"},
            })
        if system:
            blocks.append({"type": "text", "text": system})
        return blocks

    async def generate(self, prompt: str, system: str = "") -> str:
        if self._stub_mode == "echo":
            _ = self._build_system(system)
            return f"[anthropic-stub] echo: {prompt[:200]}"
        raise ProviderUnavailableError(
            "anthropic_claude",
            "stub provider; set AIP_PROVIDER_ANTHROPIC_STUB_MODE=echo for deterministic placeholder",
        )

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        if self._stub_mode == "echo":
            return {"stub": True, "provider": "anthropic_claude", "prompt_preview": prompt[:200]}
        raise ProviderUnavailableError("anthropic_claude", "stub provider; generate_json unavailable")

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        text = await self.generate(prompt, system=system)
        yield text

    async def generate_stream_typed(self, prompt: str, system: str = "") -> AsyncIterator[StreamChunk]:
        text = await self.generate(prompt, system=system)
        yield StreamChunk(kind="answer", content=text)
