"""Provider Registry.

복수 LLMProvider 인스턴스를 provider_id 로 관리한다.
Router Policy 엔진(Task 003) 이 capability 질의에 사용한다.
내부 상태는 불변 dict (copy-on-write) 로 관리.
"""

from __future__ import annotations

import logging
from typing import Iterable

from .base import LLMProvider, ProviderCapability

logger = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self, initial: Iterable[LLMProvider] | None = None) -> None:
        self._providers: dict[str, LLMProvider] = {}
        if initial:
            for p in initial:
                self._providers[p.capability.provider_id] = p

    def register(self, provider: LLMProvider) -> "ProviderRegistry":
        """불변 업데이트. 새 Registry 를 반환한다."""
        new_map = dict(self._providers)
        pid = provider.capability.provider_id
        if pid in new_map:
            logger.warning("provider.registry.override provider_id=%s", pid)
        new_map[pid] = provider
        new = ProviderRegistry.__new__(ProviderRegistry)
        new._providers = new_map
        return new

    def register_inplace(self, provider: LLMProvider) -> None:
        """빌드 단계 전용 가변 API. 외부에서는 register() 사용 권장."""
        self._providers[provider.capability.provider_id] = provider

    def get(self, provider_id: str) -> LLMProvider:
        if provider_id not in self._providers:
            raise KeyError(f"Provider not registered: {provider_id}")
        return self._providers[provider_id]

    def has(self, provider_id: str) -> bool:
        return provider_id in self._providers

    def list_available(self) -> list[ProviderCapability]:
        """stub 제외, 호출 가능한 provider capability 목록."""
        return [p.capability for p in self._providers.values() if not p.capability.stub]

    def list_all(self) -> list[ProviderCapability]:
        """stub 포함 전체."""
        return [p.capability for p in self._providers.values()]

    def ids(self) -> tuple[str, ...]:
        return tuple(self._providers.keys())
