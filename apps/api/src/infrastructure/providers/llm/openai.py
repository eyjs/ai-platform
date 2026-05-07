"""OpenAI LLM 프로바이더.

Prompt Caching: gpt-4o, gpt-4o-mini 등은 automatic prompt caching을 지원.
1024 토큰 이상의 동일 prefix가 반복되면 자동 캐시 히트.
system_prefix를 구조화하여 캐시 재사용률을 극대화한다.
"""

import json
import logging
from typing import AsyncIterator, Optional

from ..base import LLMProvider, ProviderCapability

logger = logging.getLogger(__name__)


class OpenAILLMProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 system_prefix: str = "", max_tokens: int = 4096):
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._system_prefix = system_prefix
        self._max_tokens = max_tokens

    @property
    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider_id="openai",
            supports_tool_use=True,
            supports_streaming=True,
            max_context=128000,
            cost_per_1k_tokens=0.01,
            stub=False,
            supports_prompt_caching=True,
        )

    def _build_messages(
        self,
        prompt: str,
        system: str = "",
        response_format: Optional[dict] = None,
    ) -> list[dict]:
        """메시지 배열을 구성한다. Prompt Caching 최적화 구조.

        system_prefix가 있으면 별도 content block으로 분리하여
        OpenAI automatic prompt caching의 prefix 매칭률을 높인다.
        """
        messages = []

        if self._system_prefix and system:
            # system_prefix (정적, 캐시 가능) + dynamic system을 분리 전송
            messages.append({
                "role": "system",
                "content": [
                    {"type": "text", "text": self._system_prefix},
                    {"type": "text", "text": system},
                ],
            })
        elif self._system_prefix:
            messages.append({"role": "system", "content": self._system_prefix})
        elif system:
            messages.append({"role": "system", "content": system})

        messages.append({"role": "user", "content": prompt})
        return messages

    async def generate(self, prompt: str, system: str = "") -> str:
        messages = self._build_messages(prompt, system)
        response = await self._client.chat.completions.create(
            model=self._model, messages=messages,
            max_tokens=self._max_tokens,
        )
        self._log_cache_usage(response)
        return response.choices[0].message.content or ""

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        messages = self._build_messages(prompt, system)
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=self._max_tokens,
        )
        self._log_cache_usage(response)
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        messages = self._build_messages(prompt, system)
        stream = await self._client.chat.completions.create(
            model=self._model, messages=messages, stream=True,
            max_tokens=self._max_tokens,
            stream_options={"include_usage": True},
        )
        async for chunk in stream:
            if not chunk.choices:
                # usage 청크 (마지막)
                if chunk.usage:
                    self._log_cache_usage_from_dict(chunk.usage)
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    def _log_cache_usage(self, response) -> None:
        """응답의 usage에서 cached_tokens 정보를 로깅한다."""
        usage = getattr(response, "usage", None)
        if not usage:
            return
        self._log_cache_usage_from_dict(usage)

    def _log_cache_usage_from_dict(self, usage) -> None:
        """usage 객체에서 prompt_tokens_details.cached_tokens를 추출/로깅."""
        details = getattr(usage, "prompt_tokens_details", None)
        if not details:
            return
        cached = getattr(details, "cached_tokens", 0)
        total_prompt = getattr(usage, "prompt_tokens", 0)
        if cached and cached > 0:
            logger.debug(
                "openai_prompt_cache_hit",
                extra={
                    "cached_tokens": cached,
                    "total_prompt_tokens": total_prompt,
                    "cache_hit_ratio": round(cached / total_prompt, 2) if total_prompt else 0,
                },
            )
