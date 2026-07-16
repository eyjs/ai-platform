"""Ollama LLM 프로바이더 (로컬 개발)."""

import json
import logging
from typing import AsyncIterator

import httpx

from ..base import LLMProvider, ProviderCapability

logger = logging.getLogger(__name__)

_STOP_TOKENS = ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]

# 컨텍스트 크기는 **서버가 정한다**(ollama의 OLLAMA_CONTEXT_LENGTH). 클라이언트가
# options.num_ctx로 덮지 않는다 — 그게 2026-07-16에 잡은 사고의 원인이다.
#
# ollama는 요청한 컨텍스트 크기별로 러너를 따로 띄운다. 우리는 두 표면을 쓰는데
# (OllamaProvider → /api/chat, agentic ChatOpenAI → ollama의 /v1 shim), 여기서만
# num_ctx=16384를 보내는 바람에 **매 턴 두 러너를 오가며 29.8GB 모델을 리로드**했다.
# 실측: /v1 0.55s → /api/chat(num_ctx) 7.17s → /v1 6.87s → /api/chat 6.94s.
# 리로드가 planner_timeout(5s)을 넘겨 의도 분류가 조용히 STANDALONE으로 폴백했다.
# num_ctx를 빼자 두 표면이 한 러너를 공유한다.
#
# 아래 값은 **선언용 메타데이터일 뿐**이다(ProviderCapability.max_context). 이 값으로
# 프롬프트를 잘라내는 곳은 없다(전수 확인). 진짜 상한은 DGX의 OLLAMA_CONTEXT_LENGTH다.
_DECLARED_MAX_CONTEXT = 128 * 1024


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:8b",
        system_prefix: str = "",
        connect_timeout: float = 5.0,
        read_timeout: float | None = 120.0,
        max_tokens: int | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._system_prefix = system_prefix
        # 호출부가 max_tokens를 안 주면 쓰는 기본 상한. HttpLLMProvider와 같은 의미 —
        # 폴백(Http)만 상한이 걸리고 primary(여기)는 무제한이 되는 비대칭을 막는다.
        # None이면 num_predict 미설정 = 모델 기본값(무제한).
        self._max_tokens = max_tokens
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
            max_context=_DECLARED_MAX_CONTEXT,
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
        options = {"stop": _STOP_TOKENS}
        effective_max = max_tokens if max_tokens is not None else self._max_tokens
        if effective_max is not None:
            options["num_predict"] = effective_max
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
                "options": {"stop": _STOP_TOKENS},
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
        stream_options = {"stop": _STOP_TOKENS}
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
