"""EmbeddingRouter 캐시 기능 테스트."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.embedding_router import (
    EmbeddingRouter,
    EmbeddingRouteResult,
    _TTLCache,
    _CACHE_NONE,
)


# --- _TTLCache 단위 테스트 ---


class TestTTLCache:
    def test_put_and_get(self):
        cache = _TTLCache(ttl_seconds=60.0)
        cache.put("key1", "value1")
        hit, value = cache.get("key1")
        assert hit is True
        assert value == "value1"

    def test_miss(self):
        cache = _TTLCache(ttl_seconds=60.0)
        hit, value = cache.get("nonexistent")
        assert hit is False
        assert value is None

    def test_ttl_expiry(self):
        cache = _TTLCache(ttl_seconds=0.01)  # 10ms TTL
        cache.put("key1", "value1")
        time.sleep(0.02)
        hit, value = cache.get("key1")
        assert hit is False
        assert value is None

    def test_max_entries_eviction(self):
        cache = _TTLCache(ttl_seconds=60.0, max_entries=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        # 4번째 추가 시 가장 오래된 것 제거
        cache.put("d", 4)
        assert len(cache._store) == 3
        hit, _ = cache.get("d")
        assert hit is True

    def test_expired_eviction_on_put(self):
        cache = _TTLCache(ttl_seconds=0.01, max_entries=2)
        cache.put("a", 1)
        cache.put("b", 2)
        time.sleep(0.02)
        # 만료된 엔트리가 정리되어 새 엔트리 추가 가능
        cache.put("c", 3)
        assert len(cache._store) == 1
        hit, value = cache.get("c")
        assert hit is True
        assert value == 3

    def test_none_sentinel(self):
        """_CACHE_NONE 센티널로 None 결과를 캐싱할 수 있는지 확인."""
        cache = _TTLCache(ttl_seconds=60.0)
        cache.put("null_result", _CACHE_NONE)
        hit, value = cache.get("null_result")
        assert hit is True
        assert value is _CACHE_NONE


# --- EmbeddingRouter 캐시 통합 테스트 ---


class TestEmbeddingRouterCache:
    @pytest.fixture
    def mock_embedding(self):
        provider = AsyncMock()
        provider.embed_batch = AsyncMock(return_value=[[0.1] * 10])
        return provider

    @pytest.fixture
    def router(self, mock_embedding):
        router = EmbeddingRouter(mock_embedding)
        # 수동으로 초기화 상태 설정
        router._initialized = True
        router._profile_capabilities = {
            "profile-a": [("사주 분석", [0.9] + [0.0] * 9)],
            "profile-b": [("궁합 분석", [0.0] * 9 + [0.9])],
        }
        return router

    @pytest.mark.asyncio
    async def test_cache_hit(self, router, mock_embedding):
        """동일 질문 반복 시 임베딩 호출 없이 캐시 반환."""
        # 첫 번째 호출: 캐시 미스 -> 임베딩 호출
        result1 = await router.route("사주 분석해줘")
        assert mock_embedding.embed_batch.call_count == 1

        # 두 번째 호출: 캐시 히트 -> 임베딩 호출 없음
        result2 = await router.route("사주 분석해줘")
        assert mock_embedding.embed_batch.call_count == 1  # 여전히 1회

    @pytest.mark.asyncio
    async def test_different_questions_miss(self, router, mock_embedding):
        """다른 질문은 캐시 미스."""
        await router.route("사주 분석해줘")
        await router.route("궁합 분석해줘")
        assert mock_embedding.embed_batch.call_count == 2

    @pytest.mark.asyncio
    async def test_none_result_cached(self, router, mock_embedding):
        """None 결과(임계값 미달)도 캐싱되어 반복 호출 방지."""
        # 유사도가 매우 낮은 임베딩 반환
        mock_embedding.embed_batch.return_value = [[0.0] * 10]

        result1 = await router.route("완전 관계없는 질문")
        assert result1 is None
        assert mock_embedding.embed_batch.call_count == 1

        result2 = await router.route("완전 관계없는 질문")
        assert result2 is None
        assert mock_embedding.embed_batch.call_count == 1  # 캐시 히트

    @pytest.mark.asyncio
    async def test_not_initialized(self, mock_embedding):
        """초기화되지 않은 라우터는 None 반환."""
        router = EmbeddingRouter(mock_embedding)
        result = await router.route("test")
        assert result is None
        mock_embedding.embed_batch.assert_not_called()
