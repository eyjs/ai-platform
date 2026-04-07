"""AccessPolicyStore 단위 테스트.

asyncpg.Pool을 모킹하여 DB 없이 정책 로직을 검증한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.gateway.access_policy import AccessPolicyStore


def _make_pool(rows: list[dict[str, str]]) -> AsyncMock:
    """asyncpg.Pool 모킹: fetch()가 row 목록을 반환하도록 설정."""
    pool = AsyncMock()
    # asyncpg.Record처럼 dict-like 접근을 지원하는 모킹
    records = []
    for row in rows:
        record = MagicMock()
        record.__getitem__ = lambda self, key, r=row: r[key]
        records.append(record)
    pool.fetch = AsyncMock(return_value=records)
    return pool


@pytest.mark.asyncio
async def test_no_policy_means_public():
    """정책이 없는 profile -> 모든 user_type 허용."""
    pool = _make_pool([])
    store = AccessPolicyStore(pool)
    await store.load()

    assert store.is_allowed("unknown-profile", "employee") is True
    assert store.is_allowed("unknown-profile", "customer") is True
    assert store.is_allowed("any-profile", "admin") is True


@pytest.mark.asyncio
async def test_policy_allows_matching_segment():
    """정책이 있고 user_type이 segment에 포함 -> 허용."""
    pool = _make_pool([
        {"profile_id": "hr-bot", "segment": "employee"},
        {"profile_id": "hr-bot", "segment": "manager"},
    ])
    store = AccessPolicyStore(pool)
    await store.load()

    assert store.is_allowed("hr-bot", "employee") is True
    assert store.is_allowed("hr-bot", "manager") is True


@pytest.mark.asyncio
async def test_policy_denies_non_matching_segment():
    """정책이 있고 user_type이 segment에 미포함 -> 거부."""
    pool = _make_pool([
        {"profile_id": "hr-bot", "segment": "employee"},
    ])
    store = AccessPolicyStore(pool)
    await store.load()

    assert store.is_allowed("hr-bot", "customer") is False
    assert store.is_allowed("hr-bot", "guest") is False


@pytest.mark.asyncio
async def test_empty_user_type_denied_when_policy_exists():
    """user_type이 빈 문자열이고 정책이 있으면 -> 거부."""
    pool = _make_pool([
        {"profile_id": "hr-bot", "segment": "employee"},
    ])
    store = AccessPolicyStore(pool)
    await store.load()

    assert store.is_allowed("hr-bot", "") is False


@pytest.mark.asyncio
async def test_empty_user_type_allowed_when_no_policy():
    """user_type이 빈 문자열이고 정책이 없으면 -> 허용 (전체 공개)."""
    pool = _make_pool([])
    store = AccessPolicyStore(pool)
    await store.load()

    assert store.is_allowed("any-profile", "") is True


@pytest.mark.asyncio
async def test_reload_updates_cache():
    """reload() 후 새 정책이 반영된다."""
    # 초기: hr-bot에 employee만 허용
    initial_rows = [
        {"profile_id": "hr-bot", "segment": "employee"},
    ]
    pool = _make_pool(initial_rows)
    store = AccessPolicyStore(pool)
    await store.load()

    assert store.is_allowed("hr-bot", "employee") is True
    assert store.is_allowed("hr-bot", "contractor") is False

    # 정책 변경: contractor 추가
    updated_rows = [
        {"profile_id": "hr-bot", "segment": "employee"},
        {"profile_id": "hr-bot", "segment": "contractor"},
    ]
    updated_records = []
    for row in updated_rows:
        record = MagicMock()
        record.__getitem__ = lambda self, key, r=row: r[key]
        updated_records.append(record)
    pool.fetch = AsyncMock(return_value=updated_records)

    await store.reload()

    assert store.is_allowed("hr-bot", "contractor") is True
    assert store.is_allowed("hr-bot", "employee") is True


@pytest.mark.asyncio
async def test_get_allowed_segments_returns_copy():
    """get_allowed_segments()는 내부 캐시의 복사본을 반환한다."""
    pool = _make_pool([
        {"profile_id": "hr-bot", "segment": "employee"},
        {"profile_id": "hr-bot", "segment": "manager"},
    ])
    store = AccessPolicyStore(pool)
    await store.load()

    segments = store.get_allowed_segments("hr-bot")
    assert segments == {"employee", "manager"}

    # 반환된 set을 수정해도 내부 캐시에 영향 없음
    segments.add("hacker")
    assert "hacker" not in store.get_allowed_segments("hr-bot")


@pytest.mark.asyncio
async def test_get_allowed_segments_empty_when_no_policy():
    """정책이 없는 profile의 get_allowed_segments()는 빈 set 반환."""
    pool = _make_pool([])
    store = AccessPolicyStore(pool)
    await store.load()

    assert store.get_allowed_segments("unknown") == set()


@pytest.mark.asyncio
async def test_multiple_profiles_isolated():
    """여러 profile의 정책이 서로 격리된다."""
    pool = _make_pool([
        {"profile_id": "hr-bot", "segment": "employee"},
        {"profile_id": "sales-bot", "segment": "customer"},
        {"profile_id": "sales-bot", "segment": "partner"},
    ])
    store = AccessPolicyStore(pool)
    await store.load()

    # hr-bot: employee만 허용
    assert store.is_allowed("hr-bot", "employee") is True
    assert store.is_allowed("hr-bot", "customer") is False

    # sales-bot: customer, partner만 허용
    assert store.is_allowed("sales-bot", "customer") is True
    assert store.is_allowed("sales-bot", "partner") is True
    assert store.is_allowed("sales-bot", "employee") is False

    # 정책 없는 profile: 전체 공개
    assert store.is_allowed("public-bot", "anyone") is True
