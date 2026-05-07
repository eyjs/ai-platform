"""Anthropic Claude Provider — Stub 구현.

실 SDK 미도입. 인터페이스만 완비하여 상용 전환 시점에 구현 주입.
capability.stub=True 로 표시되어 Router Policy 가 일반 호출 후보에서 제외.
"""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

from ..base import LLMProvider, ProviderCapability, ProviderUnavailableError, StreamChunk

logger = logging.getLogger(__name__)


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
