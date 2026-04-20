"""ResponseCache 테스트 (in-memory mock)."""

from __future__ import annotations

import pytest

from src.services.response_cache import ResponseCacheService
from src.services.response_cache_models import compute_cache_key, normalize_input


class TestNormalization:
    def test_nfc_strip_collapse_lower(self):
        out = normalize_input("  Hello   World\n\n")
        assert out == "hello world"

    def test_unicode_nfc(self):
        out1 = normalize_input("café")  # pre-composed
        out2 = normalize_input("cafe\u0301")  # decomposed
        assert out1 == out2

    def test_cache_key_deterministic(self):
        k1 = compute_cache_key("p1", "deterministic", "hello world")
        k2 = compute_cache_key("p1", "deterministic", "hello world")
        assert k1 == k2 and len(k1) == 64

    def test_cache_key_differs(self):
        a = compute_cache_key("p1", "deterministic", "hi")
        b = compute_cache_key("p2", "deterministic", "hi")
        assert a != b


class TestShouldCache:
    def _make_profile(self, cache_cfg):
        class P:
            config = {"cache": cache_cfg} if cache_cfg is not None else {}
        return P()

    def test_default_deterministic(self):
        svc = ResponseCacheService(session_factory=lambda: None)  # type: ignore[arg-type]
        p = self._make_profile(None)
        # No cache cfg → cached in deterministic
        class P2:
            config = {}
        assert svc.should_cache(P2(), "deterministic") is True
        assert svc.should_cache(P2(), "agentic") is False

    def test_disabled(self):
        svc = ResponseCacheService(session_factory=lambda: None)  # type: ignore[arg-type]
        p = self._make_profile({"enabled": False})
        assert svc.should_cache(p, "deterministic") is False

    def test_agentic_opt_in(self):
        svc = ResponseCacheService(session_factory=lambda: None)  # type: ignore[arg-type]
        p = self._make_profile({"enabled": True, "agentic_enabled": True})
        assert svc.should_cache(p, "agentic") is True
        p2 = self._make_profile({"enabled": True})
        assert svc.should_cache(p2, "agentic") is False
