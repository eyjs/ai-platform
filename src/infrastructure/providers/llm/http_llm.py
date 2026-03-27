"""HTTP LLM 프로바이더 (GPU 서버 / MLX 서버용).

Qwen3 등 thinking 모델의 <think>...</think> 블록을
답변과 분리하여 별도 스트림 이벤트로 전달한다.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

from ..base import LLMProvider

logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>\s*", re.DOTALL)


@dataclass(frozen=True)
class StreamChunk:
    """스트리밍 청크. kind로 thinking/answer 구분."""

    kind: str   # "thinking" | "answer"
    content: str


class HttpLLMProvider(LLMProvider):
    def __init__(self, base_url: str, system_prefix: str = "", max_tokens: int = 4096):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)
        self._system_prefix = system_prefix
        self._max_tokens = max_tokens

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

    async def generate(self, prompt: str, system: str = "") -> str:
        """답변만 반환한다 (thinking 제거)."""
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
        text = response.json()["choices"][0]["message"]["content"]
        _, answer = self.split_thinking(text)
        return answer

    async def generate_with_thinking(self, prompt: str, system: str = "") -> tuple[str, str]:
        """thinking과 answer를 분리하여 반환한다.

        Returns:
            (thinking, answer) 튜플.
        """
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
        text = response.json()["choices"][0]["message"]["content"]
        return self.split_thinking(text)

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        text = await self.generate(prompt, system)
        # LLM이 ```json ... ``` 으로 감싸는 경우 fence 제거
        stripped = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
        stripped = re.sub(r"\n?```\s*$", "", stripped)
        return json.loads(stripped)

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        """하위 호환: 답변 텍스트만 yield (thinking 제거)."""
        async for chunk in self.generate_stream_typed(prompt, system):
            if chunk.kind == "answer":
                yield chunk.content

    async def generate_stream_typed(self, prompt: str, system: str = "") -> AsyncIterator[StreamChunk]:
        """thinking/answer를 StreamChunk로 구분하여 yield."""
        system_msg = self._build_system(system)
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
