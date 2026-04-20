"""Ollama LLM 프로바이더 (로컬 개발)."""

import json
import logging
from typing import AsyncIterator

import httpx

from ..base import LLMProvider, ProviderCapability

logger = logging.getLogger(__name__)

_STOP_TOKENS = ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:8b",
        num_ctx: int = 16384,
        system_prefix: str = "",
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._num_ctx = num_ctx
        self._system_prefix = system_prefix
        self._client = httpx.AsyncClient(timeout=120.0)

    @property
    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider_id="ollama",
            supports_tool_use=False,
            supports_streaming=True,
            max_context=self._num_ctx,
            cost_per_1k_tokens=0.0,
            stub=False,
        )

    async def is_available(self) -> bool:
        try:
            r = await self._client.get(f"{self._base_url}/api/tags", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    async def generate(self, prompt: str, system: str = "") -> str:
        system_msg = self._build_system(system)
        response = await self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"num_ctx": self._num_ctx, "stop": _STOP_TOKENS},
            },
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        # /think 태그 제거
        if "</think>" in content:
            content = content.split("</think>")[-1].strip()
        return content

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        system_msg = self._build_system(system)
        response = await self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": "json",
                "options": {"num_ctx": self._num_ctx, "stop": _STOP_TOKENS},
            },
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        if "</think>" in content:
            content = content.split("</think>")[-1].strip()
        return json.loads(content)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        system_msg = self._build_system(system)
        async with self._client.stream(
            "POST",
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "stream": True,
                "options": {"num_ctx": self._num_ctx, "stop": _STOP_TOKENS},
            },
        ) as response:
            in_think = False
            async for line in response.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get("done"):
                    break
                token = data.get("message", {}).get("content", "")
                if "<think>" in token:
                    in_think = True
                    continue
                if "</think>" in token:
                    in_think = False
                    continue
                if not in_think:
                    yield token
