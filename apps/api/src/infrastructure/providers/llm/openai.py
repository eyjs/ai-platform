"""OpenAI LLM 프로바이더."""

import json
import logging
from typing import AsyncIterator

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
        )

    async def generate(self, prompt: str, system: str = "") -> str:
        system_msg = self._build_system(system)
        messages = []
        if system_msg:
            messages.append({"role": "system", "content": system_msg})
        messages.append({"role": "user", "content": prompt})
        response = await self._client.chat.completions.create(
            model=self._model, messages=messages,
            max_tokens=self._max_tokens,
        )
        return response.choices[0].message.content or ""

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        system_msg = self._build_system(system)
        messages = []
        if system_msg:
            messages.append({"role": "system", "content": system_msg})
        messages.append({"role": "user", "content": prompt})
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=self._max_tokens,
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        system_msg = self._build_system(system)
        messages = []
        if system_msg:
            messages.append({"role": "system", "content": system_msg})
        messages.append({"role": "user", "content": prompt})
        stream = await self._client.chat.completions.create(
            model=self._model, messages=messages, stream=True,
            max_tokens=self._max_tokens,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
