"""Anthropic Claude Provider.

- AnthropicLLMProvider: 실 SDK(anthropic.AsyncAnthropic) 구현. 프로덕션 메인/라우터 LLM.
- AnthropicStubProvider: SDK 미설치/개발용 스텁(capability.stub=True).

기본 모델은 비용 최적 claude-haiku-4-5. Prompt Caching(system prefix에
cache_control ephemeral)으로 반복 prefix 비용을 ~0.1x로 절감한다.

## Prompt Caching 블록 구조 (task-100)
블록 순서: [system_prefix(cache)] → [cacheable_system(cache)] → [volatile_system(no-cache)]
cache_control ephemeral 은 cacheable 경계 마지막 블록(cacheable_system 또는 system_prefix)에만 부여.
volatile_system (날짜/per-turn 등) 은 캐시 밖에 두어 캐시 무효화를 방지.

## 하위호환 정책
기존 단일 `system: str` 인자 호출은 volatile_system 으로 처리 — 캐시 효율은 낮지만
기존 테스트/호출부가 수정 없이 동작한다. 캐싱을 활용하려면 cacheable_system 으로 전달할 것.
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

# Anthropic Haiku 프롬프트 캐싱 최소 토큰 수
_CACHE_MIN_TOKENS_HAIKU = 4096


def _estimate_tokens(text: str) -> int:
    """보수적 토큰 수 근사 (char/4). Anthropic count_tokens API 대체."""
    return max(1, len(text) // 4)


def _warn_if_cacheable_too_small(cacheable_text: str) -> None:
    """cacheable 블록 토큰 수 검증 — 4096(Haiku 최소) 미달 시 경고.

    실 패딩/구조조정은 호출부(task-101 소관). 여기서는 측정·경고만 수행.
    """
    estimated = _estimate_tokens(cacheable_text)
    if estimated < _CACHE_MIN_TOKENS_HAIKU:
        logger.warning(
            "anthropic_cache_too_small",
            extra={
                "estimated_tokens": estimated,
                "min_required": _CACHE_MIN_TOKENS_HAIKU,
                "hint": "cacheable_system 이 4096 토큰 미만 — 캐시 히트율 저하 우려. "
                        "task-101 에서 grounding 패딩 적용 예정.",
            },
        )


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

    def _system_kwarg(
        self,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> dict:
        """messages.create 의 system 인자를 구성한다(Prompt Caching).

        블록 순서:
          1. system_prefix       → cache_control ephemeral (생성자 고정값)
          2. cacheable_system    → cache_control ephemeral (페르소나+grounding, 세션 안정)
          3. volatile_system     → no cache (날짜/per-turn, 캐시 경계 밖)

        cache_control 은 캐시 가능 마지막 블록(cacheable_system 또는 system_prefix)에만 부여.
        셋 다 비면 {} 반환(기존 동작 보존).

        하위호환: `system` 단일 인자 → volatile_system 으로 취급(기존 호출 무수정 동작).
        cacheable_system/volatile_system 을 명시하면 `system` 은 무시한다.
        """
        # 하위호환: 신규 인자가 모두 비어있으면 기존 system 을 volatile 로 사용
        if not cacheable_system and not volatile_system:
            volatile_system = system

        blocks: list[dict] = []

        # 1. system_prefix — 생성자 고정값, 캐시 가능 후보
        if self._system_prefix:
            blocks.append({
                "type": "text",
                "text": self._system_prefix,
                # cache_control 은 나중에 cacheable_system 이 없을 때만 여기에 붙임
            })

        # 2. cacheable_system — 페르소나+grounding, 캐시 경계
        if cacheable_system:
            _warn_if_cacheable_too_small(
                (self._system_prefix or "") + cacheable_system
            )
            blocks.append({
                "type": "text",
                "text": cacheable_system,
            })

        # cache_control 은 캐시 가능 마지막 블록에만 부여
        if blocks:
            blocks[-1]["cache_control"] = {"type": "ephemeral"}

        # 3. volatile_system — 날짜/per-turn, 캐시 밖
        if volatile_system:
            blocks.append({"type": "text", "text": volatile_system})

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

    async def generate(
        self,
        prompt: str,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> str:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **self._system_kwarg(system, cacheable_system, volatile_system),
        )
        self._log_cache_usage(getattr(resp, "usage", None))
        return "".join(b.text for b in resp.content if b.type == "text")

    async def generate_json(
        self,
        prompt: str,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> dict:
        # 스키마 비의존 generate_json: JSON-only 지시 후 파싱(모델 무관 견고).
        json_system = "You must respond with a single valid JSON object only — no prose, no code fences."
        if cacheable_system or volatile_system:
            # 신규 인자 사용: json_system 을 volatile 에 추가
            merged_volatile = f"{volatile_system}\n\n{json_system}" if volatile_system else json_system
            text = (
                await self.generate(
                    prompt,
                    cacheable_system=cacheable_system,
                    volatile_system=merged_volatile,
                )
            ).strip()
        else:
            # 하위호환: 기존 단일 system 경로
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

    async def generate_stream(
        self,
        prompt: str,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **self._system_kwarg(system, cacheable_system, volatile_system),
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            self._log_cache_usage(getattr(final, "usage", None))

    async def generate_stream_typed(
        self,
        prompt: str,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> AsyncIterator[StreamChunk]:
        async for token in self.generate_stream(
            prompt,
            system=system,
            cacheable_system=cacheable_system,
            volatile_system=volatile_system,
        ):
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

    def _build_system_blocks(
        self,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> list[dict]:
        """Anthropic Prompt Caching용 system 블록 구성.

        블록 순서:
          1. system_prefix       → cache_control ephemeral (생성자 고정값)
          2. cacheable_system    → cache_control ephemeral (페르소나+grounding)
          3. volatile_system     → no cache (날짜/per-turn)

        cache_control 은 캐시 가능 마지막 블록에만 부여.

        하위호환: cacheable_system/volatile_system 미지정 시 system → volatile_system 로 취급.
        """
        # 하위호환: 신규 인자가 모두 비어있으면 기존 system 을 volatile 로 사용
        if not cacheable_system and not volatile_system:
            volatile_system = system

        blocks: list[dict] = []

        # 1. system_prefix — 생성자 고정값
        if self._system_prefix:
            blocks.append({
                "type": "text",
                "text": self._system_prefix,
            })

        # 2. cacheable_system — 페르소나+grounding
        if cacheable_system:
            _warn_if_cacheable_too_small(
                (self._system_prefix or "") + cacheable_system
            )
            blocks.append({
                "type": "text",
                "text": cacheable_system,
            })

        # cache_control 은 캐시 가능 마지막 블록에만 부여
        if blocks:
            blocks[-1]["cache_control"] = {"type": "ephemeral"}

        # 3. volatile_system — no cache
        if volatile_system:
            blocks.append({"type": "text", "text": volatile_system})

        return blocks

    async def generate(
        self,
        prompt: str,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> str:
        if self._stub_mode == "echo":
            _ = self._build_system_blocks(system, cacheable_system, volatile_system)
            return f"[anthropic-stub] echo: {prompt[:200]}"
        raise ProviderUnavailableError(
            "anthropic_claude",
            "stub provider; set AIP_PROVIDER_ANTHROPIC_STUB_MODE=echo for deterministic placeholder",
        )

    async def generate_json(
        self,
        prompt: str,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> dict:
        if self._stub_mode == "echo":
            return {"stub": True, "provider": "anthropic_claude", "prompt_preview": prompt[:200]}
        raise ProviderUnavailableError("anthropic_claude", "stub provider; generate_json unavailable")

    async def generate_stream(
        self,
        prompt: str,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> AsyncIterator[str]:
        text = await self.generate(prompt, system=system, cacheable_system=cacheable_system, volatile_system=volatile_system)
        yield text

    async def generate_stream_typed(
        self,
        prompt: str,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> AsyncIterator[StreamChunk]:
        text = await self.generate(prompt, system=system, cacheable_system=cacheable_system, volatile_system=volatile_system)
        yield StreamChunk(kind="answer", content=text)
