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
        connect_timeout: float = 5.0,
        read_timeout: float | None = 120.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._num_ctx = num_ctx
        self._system_prefix = system_prefix
        # 타임아웃 두 축을 분리한다:
        #  - connect: 짧게. 원격(DGX 등)이 오프라인이면 SYN 무응답으로 connect가
        #    hang → 짧아야 다운 즉시 감지 → FailoverLLMProvider가 초 단위로 폴백.
        #  - read: 길게/무제한(None). 복잡한 쿼리는 생성에 수 분~수십 분 걸릴 수 있어
        #    짧으면 정상 생성이 중간에 잘린다. 스트리밍은 청크 간 간격에 적용되므로
        #    토큰이 흐르는 한 만료되지 않는다.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout, read=read_timeout, write=10.0, pool=5.0,
            )
        )

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

    async def generate(
        self, prompt: str, system: str = "", max_tokens: int | None = None,
        cacheable_system: str = "", volatile_system: str = "",
    ) -> str:
        system_msg = self._build_system(
            self._combine_system(system, cacheable_system, volatile_system)
        )
        options = {"num_ctx": self._num_ctx, "stop": _STOP_TOKENS}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        response = await self._client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "think": False,
                "options": options,
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
                "think": False,
                "format": "json",
                "options": {"num_ctx": self._num_ctx, "stop": _STOP_TOKENS},
            },
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        if "</think>" in content:
            content = content.split("</think>")[-1].strip()
        return json.loads(content)

    async def generate_stream(
        self, prompt: str, system: str = "", max_tokens: int | None = None,
        cacheable_system: str = "", volatile_system: str = "",
    ) -> AsyncIterator[str]:
        system_msg = self._build_system(
            self._combine_system(system, cacheable_system, volatile_system)
        )
        stream_options = {"num_ctx": self._num_ctx, "stop": _STOP_TOKENS}
        if max_tokens is not None:
            stream_options["num_predict"] = max_tokens
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
                "think": False,
                "options": stream_options,
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
                # thinking 모델(qwen3 계열)은 사고를 message.thinking으로 보내고
                # content=""인 청크를 다수 방출 — 빈 토큰은 스킵(빈 답변 실사고).
                if not token:
                    continue
                if "<think>" in token:
                    in_think = True
                    continue
                if "</think>" in token:
                    in_think = False
                    continue
                if not in_think:
                    yield token
