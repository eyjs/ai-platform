"""Provider Policy / Router 단위 테스트."""

from __future__ import annotations

import pytest

from src.infrastructure.providers.base import (
    LLMProvider,
    ProviderCapability,
    ProviderUnavailableError,
)
from src.infrastructure.providers.registry import ProviderRegistry
from src.router.provider_policy import (
    ProviderCandidate,
    ProviderPolicy,
    parse_policy,
    select_fallback_chain,
    select_primary,
)
from src.router.provider_router import ProviderRouter


class FakeLLM(LLMProvider):
    def __init__(self, pid: str, tool_use: bool = False, cost: float = 0.0,
                 fail: bool = False, stub: bool = False):
        self._pid = pid
        self._tool_use = tool_use
        self._cost = cost
        self._fail = fail
        self._stub = stub

    @property
    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider_id=self._pid,
            supports_tool_use=self._tool_use,
            supports_streaming=True,
            max_context=8192,
            cost_per_1k_tokens=self._cost,
            stub=self._stub,
        )

    async def generate(self, prompt, system=""):
        if self._fail:
            raise RuntimeError(f"{self._pid} failed")
        return f"{self._pid}:ok"

    async def generate_json(self, prompt, system=""):
        if self._fail:
            raise RuntimeError(f"{self._pid} failed")
        return {"provider": self._pid}


class TestParsePolicy:
    def test_empty_returns_default(self):
        policy = parse_policy(None)
        assert policy.candidates[0].provider_id == "__default__"

    def test_no_providers_block(self):
        policy = parse_policy({"mode": "deterministic"})
        assert policy.candidates[0].provider_id == "__default__"

    def test_with_candidates(self):
        cfg = {
            "providers": {
                "candidates": [
                    {"provider_id": "anthropic_claude", "priority": 10, "require_tool_use": True},
                    {"provider_id": "ollama", "priority": 100},
                ],
                "fallback_on": ["timeout", "5xx"],
                "max_fallback_depth": 1,
            }
        }
        policy = parse_policy(cfg)
        assert len(policy.candidates) == 2
        assert policy.candidates[0].provider_id == "anthropic_claude"
        assert policy.candidates[0].require_tool_use is True
        assert policy.fallback_on == ("timeout", "5xx")
        assert policy.max_fallback_depth == 1

    def test_depth_clamped(self):
        cfg = {"providers": {"candidates": [{"provider_id": "a"}], "max_fallback_depth": 99}}
        policy = parse_policy(cfg)
        assert policy.max_fallback_depth == 2


class TestSelectPrimary:
    def test_empty_available(self):
        policy = ProviderPolicy(candidates=(ProviderCandidate("a"),))
        assert select_primary(policy, []) is None

    def test_priority_order(self):
        cfg = {
            "providers": {
                "candidates": [
                    {"provider_id": "low", "priority": 100},
                    {"provider_id": "high", "priority": 10},
                ]
            }
        }
        policy = parse_policy(cfg)
        available = [
            FakeLLM("low").capability,
            FakeLLM("high").capability,
        ]
        primary = select_primary(policy, available)
        assert primary is not None and primary.provider_id == "high"

    def test_require_tool_use_filters(self):
        cfg = {
            "providers": {
                "candidates": [
                    {"provider_id": "ollama", "priority": 1, "require_tool_use": True},
                ]
            }
        }
        policy = parse_policy(cfg)
        available = [FakeLLM("ollama", tool_use=False).capability]
        assert select_primary(policy, available) is None

    def test_max_cost_filter(self):
        cfg = {
            "providers": {
                "candidates": [
                    {"provider_id": "openai", "priority": 1, "max_cost_per_1k": 0.005},
                ]
            }
        }
        policy = parse_policy(cfg)
        avail = [FakeLLM("openai", cost=0.01).capability]  # 0.01 > 0.005 → reject
        assert select_primary(policy, avail) is None
        avail2 = [FakeLLM("openai", cost=0.001).capability]
        assert select_primary(policy, avail2) is not None


class TestFallbackChain:
    def test_excludes_primary(self):
        cfg = {
            "providers": {
                "candidates": [
                    {"provider_id": "a", "priority": 1},
                    {"provider_id": "b", "priority": 2},
                    {"provider_id": "c", "priority": 3},
                ]
            }
        }
        policy = parse_policy(cfg)
        available = [FakeLLM("a").capability, FakeLLM("b").capability, FakeLLM("c").capability]
        chain = select_fallback_chain(policy, available, exclude_ids=("a",))
        assert [c.provider_id for c in chain] == ["b", "c"]

    def test_respects_depth(self):
        cfg = {
            "providers": {
                "candidates": [
                    {"provider_id": "a", "priority": 1},
                    {"provider_id": "b", "priority": 2},
                    {"provider_id": "c", "priority": 3},
                ],
                "max_fallback_depth": 1,
            }
        }
        policy = parse_policy(cfg)
        available = [FakeLLM("a").capability, FakeLLM("b").capability, FakeLLM("c").capability]
        chain = select_fallback_chain(policy, available, exclude_ids=("a",))
        assert len(chain) == 1 and chain[0].provider_id == "b"


class TestProviderRouter:
    @pytest.mark.asyncio
    async def test_invoke_success_primary(self):
        reg = ProviderRegistry()
        reg.register_inplace(FakeLLM("a"))
        router = ProviderRouter(reg, default_provider_id="a")
        resolved, out = await router.invoke_with_fallback(
            profile_config=None,
            call_fn=lambda llm: llm.generate("hi"),
        )
        assert resolved.provider_id == "a"
        assert out == "a:ok"

    @pytest.mark.asyncio
    async def test_fallback_chain(self):
        reg = ProviderRegistry()
        reg.register_inplace(FakeLLM("a", fail=True))
        reg.register_inplace(FakeLLM("b", fail=False))
        cfg = {"providers": {"candidates": [
            {"provider_id": "a", "priority": 1},
            {"provider_id": "b", "priority": 2},
        ]}}
        router = ProviderRouter(reg)
        resolved, out = await router.invoke_with_fallback(
            profile_config=cfg,
            call_fn=lambda llm: llm.generate("hi"),
        )
        assert resolved.provider_id == "b"
        assert out == "b:ok"

    @pytest.mark.asyncio
    async def test_all_fail_raises(self):
        reg = ProviderRegistry()
        reg.register_inplace(FakeLLM("a", fail=True))
        reg.register_inplace(FakeLLM("b", fail=True))
        cfg = {"providers": {"candidates": [
            {"provider_id": "a", "priority": 1},
            {"provider_id": "b", "priority": 2},
        ]}}
        router = ProviderRouter(reg)
        with pytest.raises(ProviderUnavailableError):
            await router.invoke_with_fallback(cfg, lambda llm: llm.generate("hi"))
