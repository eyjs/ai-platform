"""S1~S6 시나리오 통합 테스트 (Task 013).

이 테스트는 각 시나리오의 핵심 동작을 가볍게 검증한다.
실제 DB 가 없는 환경에서도 동작하도록 mock / 순수 로직 위주로 구성.
"""

from __future__ import annotations

import os

import pytest

from src.infrastructure.providers.base import (
    LLMProvider,
    ProviderCapability,
    ProviderUnavailableError,
)
from src.infrastructure.providers.registry import ProviderRegistry
from src.infrastructure.providers.llm.anthropic import AnthropicStubProvider
from src.router.provider_policy import parse_policy, select_primary, select_fallback_chain
from src.router.provider_router import ProviderRouter
from src.services.response_cache_models import (
    compute_cache_key,
    normalize_input,
)


class _Dummy(LLMProvider):
    def __init__(self, pid, cost=0.0, tool_use=False, fail=False, stub=False):
        self._pid = pid
        self._cost = cost
        self._tool_use = tool_use
        self._fail = fail
        self._stub = stub

    @property
    def capability(self):
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
            raise RuntimeError(f"{self._pid} down")
        return f"{self._pid}:{prompt}"

    async def generate_json(self, prompt, system=""):
        return {}


# ---------------- S1: Provider 전환 ----------------

class TestS1ProviderSwitch:
    def test_env_default_development(self):
        assert os.environ.get("AIP_PROVIDER_MODE") == "development"

    def test_anthropic_stub_listed_but_not_in_available(self):
        reg = ProviderRegistry()
        reg.register_inplace(_Dummy("ollama"))
        reg.register_inplace(AnthropicStubProvider())
        avail = [c.provider_id for c in reg.list_available()]
        all_ids = [c.provider_id for c in reg.list_all()]
        assert "anthropic_claude" in all_ids
        assert "anthropic_claude" not in avail  # stub 은 제외
        assert "ollama" in avail

    def test_capability_metadata_populated(self):
        stub = AnthropicStubProvider()
        assert stub.capability.supports_tool_use is True
        assert stub.capability.max_context == 200000
        assert stub.capability.cost_per_1k_tokens > 0


# ---------------- S2: API Key 관리 ----------------

class TestS2ApiKey:
    """BFF 레벨 E2E 는 별도 supertest. 여기서는 contract 호환성만 smoke."""

    def test_sha256_hex_length_contract(self):
        import hashlib
        h = hashlib.sha256(b"aip_dev_admin").hexdigest()
        assert len(h) == 64


# ---------------- S3: Profile YAML Schema ----------------

class TestS3ProfileSchema:
    def test_schema_exists(self):
        import json, pathlib
        schema_path = pathlib.Path(".pipeline/contracts/profile-yaml-schema.json")
        if not schema_path.exists():
            pytest.skip("schema file not present in this checkout")
        data = json.loads(schema_path.read_text(encoding="utf-8"))
        assert data["type"] == "object"
        assert "mode" in data["properties"]


# ---------------- S4: 요청 로그 ----------------

class TestS4RequestLog:
    def test_enqueue_non_blocking(self):
        from src.observability.request_log_service import RequestLogService
        from src.observability.request_log_models import RequestLogEntry
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_session():
            class S:
                async def execute(self, *a, **kw): ...
                async def commit(self): ...
            yield S()

        svc = RequestLogService(fake_session, max_queue=5)
        for i in range(3):
            svc.enqueue(RequestLogEntry(status_code=200, latency_ms=i))
        # 예외 없이 종료하면 통과


# ---------------- S5: 응답 캐싱 ----------------

class TestS5ResponseCache:
    def test_key_normalization(self):
        a = compute_cache_key("p1", "deterministic", normalize_input("  Hello  World\n"))
        b = compute_cache_key("p1", "deterministic", normalize_input("hello world"))
        assert a == b

    def test_should_cache_deterministic_default(self):
        from src.services.response_cache import ResponseCacheService
        svc = ResponseCacheService(session_factory=lambda: None)

        class P:
            config = {}
        assert svc.should_cache(P(), "deterministic") is True
        assert svc.should_cache(P(), "agentic") is False


# ---------------- S6: Provider 라우팅 정책 ----------------

class TestS6ProviderRouting:
    def test_priority_then_fallback(self):
        cfg = {"providers": {"candidates": [
            {"provider_id": "primary", "priority": 1},
            {"provider_id": "backup", "priority": 2},
        ]}}
        policy = parse_policy(cfg)
        avail = [_Dummy("primary").capability, _Dummy("backup").capability]
        p = select_primary(policy, avail)
        assert p is not None and p.provider_id == "primary"
        chain = select_fallback_chain(policy, avail, exclude_ids=("primary",))
        assert [c.provider_id for c in chain] == ["backup"]

    @pytest.mark.asyncio
    async def test_invoke_with_fallback_recovers(self):
        reg = ProviderRegistry()
        reg.register_inplace(_Dummy("primary", fail=True))
        reg.register_inplace(_Dummy("backup", fail=False))
        cfg = {"providers": {"candidates": [
            {"provider_id": "primary", "priority": 1},
            {"provider_id": "backup", "priority": 2},
        ]}}
        router = ProviderRouter(reg)
        resolved, out = await router.invoke_with_fallback(
            cfg, lambda llm: llm.generate("hi"),
        )
        assert resolved.provider_id == "backup"

    @pytest.mark.asyncio
    async def test_all_fail_raises(self):
        reg = ProviderRegistry()
        reg.register_inplace(_Dummy("a", fail=True))
        reg.register_inplace(_Dummy("b", fail=True))
        cfg = {"providers": {"candidates": [
            {"provider_id": "a", "priority": 1},
            {"provider_id": "b", "priority": 2},
        ]}}
        router = ProviderRouter(reg)
        with pytest.raises(ProviderUnavailableError):
            await router.invoke_with_fallback(cfg, lambda llm: llm.generate("hi"))


# ---------------- Conftest 자체 검증 ----------------

class TestConftestBlocking:
    def test_commercial_env_stripped(self):
        assert os.environ.get("ANTHROPIC_API_KEY") is None
        assert os.environ.get("OPENAI_API_KEY") is None
        assert os.environ.get("GOOGLE_API_KEY") is None

    @pytest.mark.asyncio
    async def test_outbound_anthropic_blocked(self):
        import httpx
        with pytest.raises(Exception):
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.get("https://api.anthropic.com/v1/messages")
