"""T1: GraphCache 단위 테스트 — put/get, TTL, invalidate, LRU."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from src.router.graph_cache import GraphCache, CacheKey


@pytest.fixture
def cache() -> GraphCache:
    return GraphCache(ttl_seconds=1.0, max_entries=5)


def test_put_and_get(cache: GraphCache):
    """put 후 get으로 조회한다."""
    graph = MagicMock()
    key = GraphCache.make_key("system prompt", ["tool_a", "tool_b"])
    cache.put(key, graph)

    result = cache.get(key)
    assert result is graph


def test_get_missing_returns_none(cache: GraphCache):
    """존재하지 않는 키는 None을 반환한다."""
    key = GraphCache.make_key("nonexistent", ["tool_x"])
    assert cache.get(key) is None


def test_ttl_expiry(cache: GraphCache):
    """TTL 만료 후 get은 None을 반환한다 (lazy eviction)."""
    graph = MagicMock()
    key = GraphCache.make_key("system", ["tool_a"])
    cache.put(key, graph)

    # TTL 1초 설정 — 시간 조작
    with patch("src.router.graph_cache.time.time", return_value=time.time() + 2.0):
        result = cache.get(key)

    assert result is None
    assert cache.size == 0


def test_invalidate_by_profile_id(cache: GraphCache):
    """profile_id로 연관 항목만 제거한다."""
    key1 = GraphCache.make_key("prompt_a", ["tool_1"])
    key2 = GraphCache.make_key("prompt_b", ["tool_2"])
    key3 = GraphCache.make_key("prompt_c", ["tool_3"])

    cache.put(key1, MagicMock(), profile_id="profile_x")
    cache.put(key2, MagicMock(), profile_id="profile_x")
    cache.put(key3, MagicMock(), profile_id="profile_y")

    removed = cache.invalidate("profile_x")
    assert removed == 2
    assert cache.size == 1
    assert cache.get(key3) is not None


def test_invalidate_all(cache: GraphCache):
    """모든 캐시를 제거한다."""
    for i in range(3):
        key = GraphCache.make_key(f"prompt_{i}", [f"tool_{i}"])
        cache.put(key, MagicMock())

    removed = cache.invalidate_all()
    assert removed == 3
    assert cache.size == 0


def test_max_entries_lru_eviction(cache: GraphCache):
    """max_entries 초과 시 LRU 방식으로 가장 오래된 항목을 제거한다."""
    # max_entries=5
    keys = []
    for i in range(5):
        key = GraphCache.make_key(f"prompt_{i}", [f"tool_{i}"])
        cache.put(key, MagicMock())
        keys.append(key)

    assert cache.size == 5

    # key[0]에 접근하여 last_accessed 갱신
    cache.get(keys[0])

    # 6번째 항목 추가 -> key[1]이 LRU로 제거됨 (key[0]은 방금 접근)
    new_key = GraphCache.make_key("prompt_new", ["tool_new"])
    cache.put(new_key, MagicMock())

    assert cache.size == 5
    assert cache.get(keys[1]) is None  # 제거됨
    assert cache.get(keys[0]) is not None  # 유지됨
    assert cache.get(new_key) is not None  # 새로 추가됨


def test_make_key_sorted_tool_names():
    """tool_names는 정렬되어 순서 독립적이다."""
    key1 = GraphCache.make_key("prompt", ["b", "a", "c"])
    key2 = GraphCache.make_key("prompt", ["c", "a", "b"])
    assert key1 == key2


def test_make_key_different_prompts():
    """system_prompt가 다르면 다른 키."""
    key1 = GraphCache.make_key("prompt_1", ["tool_a"])
    key2 = GraphCache.make_key("prompt_2", ["tool_a"])
    assert key1 != key2


def test_make_key_different_tools():
    """tool_names가 다르면 다른 키."""
    key1 = GraphCache.make_key("prompt", ["tool_a"])
    key2 = GraphCache.make_key("prompt", ["tool_b"])
    assert key1 != key2


def test_size_property(cache: GraphCache):
    """size는 현재 엔트리 수를 반환한다."""
    assert cache.size == 0

    key = GraphCache.make_key("p", ["t"])
    cache.put(key, MagicMock())
    assert cache.size == 1


def test_put_same_key_overwrites(cache: GraphCache):
    """동일 키로 put하면 덮어쓴다."""
    key = GraphCache.make_key("p", ["t"])
    graph1 = MagicMock()
    graph2 = MagicMock()

    cache.put(key, graph1)
    cache.put(key, graph2)

    assert cache.size == 1
    assert cache.get(key) is graph2
