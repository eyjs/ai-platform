"""Provider Capability / Registry 테스트.

AnthropicStub 테스트는 2026-07-16 상용 퇴역과 함께 지웠다 — 그 스텁은 상용 벤더를
레지스트리에서 흉내 내려고 존재했고, 흉내 낼 벤더가 없어졌다. stub 제외 규칙 자체는
아래 TestProviderRegistry(DummyProvider stub=True)가 벤더와 무관하게 검증한다.
"""

from __future__ import annotations

import pytest

from src.infrastructure.providers.base import (
    LLMProvider,
    ProviderCapability,
)
from src.infrastructure.providers.registry import ProviderRegistry


class DummyProvider(LLMProvider):
    def __init__(self, pid: str = "dummy", stub: bool = False):
        self._pid = pid
        self._stub = stub

    @property
    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider_id=self._pid,
            supports_tool_use=True,
            supports_streaming=True,
            max_context=4096,
            cost_per_1k_tokens=0.0,
            stub=self._stub,
        )

    async def generate(self, prompt: str, system: str = "") -> str:
        return "ok"

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        return {"ok": True}


class TestProviderCapability:
    def test_capability_fields(self):
        cap = DummyProvider().capability
        assert cap.provider_id == "dummy"
        assert cap.supports_tool_use is True
        assert cap.max_context == 4096
        assert cap.stub is False


class TestProviderRegistry:
    def test_register_get(self):
        reg = ProviderRegistry()
        reg.register_inplace(DummyProvider("a"))
        reg.register_inplace(DummyProvider("b"))
        assert reg.has("a")
        assert reg.has("b")
        assert reg.get("a").capability.provider_id == "a"

    def test_list_available_excludes_stub(self):
        reg = ProviderRegistry()
        reg.register_inplace(DummyProvider("real", stub=False))
        reg.register_inplace(DummyProvider("stub", stub=True))
        ids = [c.provider_id for c in reg.list_available()]
        assert ids == ["real"]
        all_ids = [c.provider_id for c in reg.list_all()]
        assert set(all_ids) == {"real", "stub"}

    def test_register_is_copy_on_write(self):
        reg = ProviderRegistry()
        reg2 = reg.register(DummyProvider("x"))
        assert not reg.has("x")
        assert reg2.has("x")

    def test_get_missing_raises(self):
        reg = ProviderRegistry()
        with pytest.raises(KeyError):
            reg.get("missing")
