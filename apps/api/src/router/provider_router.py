"""Provider Router — 정책 엔진을 registry 에 연결.

- resolve(): profile policy + registry 조회 → ResolvedProvider
- invoke_with_fallback(): 주 → 후보 순 시도. 실패 시 다음 후보로 재시도. 최대 2단.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from src.infrastructure.providers.base import (
    LLMProvider,
    ProviderCapability,
    ProviderUnavailableError,
)
from src.infrastructure.providers.registry import ProviderRegistry

from .provider_policy import (
    ProviderPolicy,
    parse_policy,
    select_fallback_chain,
    select_primary,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedProvider:
    provider_id: str
    capability: ProviderCapability
    llm: LLMProvider


class ProviderRouter:
    def __init__(self, registry: ProviderRegistry, default_provider_id: Optional[str] = None):
        self._registry = registry
        self._default = default_provider_id

    def _materialize_policy(self, policy: ProviderPolicy) -> ProviderPolicy:
        """__default__ placeholder 를 실제 default provider id 로 치환."""
        if self._default is None:
            return policy
        new_cands = tuple(
            (c if c.provider_id != "__default__" else
             type(c)(
                provider_id=self._default,
                priority=c.priority,
                require_tool_use=c.require_tool_use,
                require_streaming=c.require_streaming,
                max_cost_per_1k=c.max_cost_per_1k,
             ))
            for c in policy.candidates
        )
        return ProviderPolicy(
            candidates=new_cands,
            fallback_on=policy.fallback_on,
            max_fallback_depth=policy.max_fallback_depth,
        )

    async def resolve(self, profile_config: dict | None, preferred_mode: str) -> ResolvedProvider:
        policy = self._materialize_policy(parse_policy(profile_config))
        available = self._registry.list_available()
        primary = select_primary(policy, available)
        if primary is None:
            raise ProviderUnavailableError("none", "no candidate matches policy")
        llm = self._registry.get(primary.provider_id)
        return ResolvedProvider(
            provider_id=primary.provider_id,
            capability=primary,
            llm=llm,
        )

    async def invoke_with_fallback(
        self,
        profile_config: dict | None,
        call_fn: Callable[[LLMProvider], Awaitable[str]],
    ) -> tuple[ResolvedProvider, str]:
        """주 provider 호출. 실패 시 fallback chain 따라 최대 2단 재시도.

        Returns:
            (최종 성공한 ResolvedProvider, 응답 문자열)
        Raises:
            ProviderUnavailableError: 전 chain 실패 시
        """
        policy = self._materialize_policy(parse_policy(profile_config))
        available = self._registry.list_available()

        primary = select_primary(policy, available)
        if primary is None:
            raise ProviderUnavailableError("none", "no candidate matches policy")

        attempts: list[ProviderCapability] = [primary]
        chain = select_fallback_chain(
            policy, available, exclude_ids=(primary.provider_id,)
        )
        attempts.extend(chain)

        last_error: Optional[Exception] = None
        for cap in attempts:
            llm = self._registry.get(cap.provider_id)
            try:
                result = await call_fn(llm)
                resolved = ResolvedProvider(
                    provider_id=cap.provider_id, capability=cap, llm=llm,
                )
                if last_error is not None:
                    logger.info(
                        "provider.fallback.success after_provider=%s recovered_from=%s",
                        cap.provider_id, type(last_error).__name__,
                    )
                return resolved, result
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "provider.invoke.failed provider_id=%s error_type=%s error=%s",
                    cap.provider_id, type(exc).__name__, str(exc)[:200],
                )
                logger.info(
                    "provider.fallback.attempt from=%s remaining=%d",
                    cap.provider_id, len(attempts) - attempts.index(cap) - 1,
                )

        raise ProviderUnavailableError(
            "all",
            f"all {len(attempts)} providers failed; last_error={type(last_error).__name__ if last_error else 'unknown'}",
        )
