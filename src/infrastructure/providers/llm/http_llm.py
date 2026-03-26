"""HTTP LLM 프로바이더 (GPU 서버 / MLX 서버용)."""

import json
import logging
import re
from typing import AsyncIterator

import httpx

from ..base import LLMProvider

logger = logging.getLogger(__name__)


class HttpLLMProvider(LLMProvider):
    def __init__(self, base_url: str, system_prefix: str = "", max_tokens: int = 4096):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)
        self._system_prefix = system_prefix
        self._max_tokens = max_tokens

    async def close(self) -> None:
        await self._client.aclose()

    async def generate(self, prompt: str, system: str = "") -> str:
        system_msg = self._build_system(system)
        response = await self._client.post(
            f"{self._base_url}/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "max_tokens": self._max_tokens,
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        text = await self.generate(prompt, system)
        # LLM이 ```json ... ``` 으로 감싸는 경우 fence 제거
        stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
        stripped = re.sub(r"\n?```\s*$", "", stripped)
        return json.loads(stripped)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        system_msg = self._build_system(system)
        async with self._client.stream(
            "POST",
            f"{self._base_url}/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "stream": True,
                "max_tokens": self._max_tokens,
            },
        ) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                data = json.loads(data_str)
                delta = data.get("choices", [{}])[0].get("delta", {})
                if content := delta.get("content"):
                    yield content
