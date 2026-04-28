"""Memory System 통합 테스트.

MemoryStore save/get/cleanup, ScopedMemoryLoader, Agent memory 자동 주입.
asyncpg.Pool을 목킹하여 DB 없이도 로직 검증.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.memory.memory_store import MemoryStore
from src.infrastructure.memory.scoped_memory import MemoryBundle, ScopedMemoryLoader


def _make_pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="DELETE 0")
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


class TestMemoryStore:
    @pytest.fixture
    def pool(self):
        return _make_pool()

    @pytest.fixture
    def store(self, pool):
        return MemoryStore(pool)

    async def test_save_memory_dict_value(self, store, pool):
        await store.save_memory("t1", "key1", {"foo": "bar"}, "fact")
        pool.execute.assert_awaited_once()
        args = pool.execute.call_args[0]
        assert args[1] == "t1"
        assert args[2] == "key1"
        assert json.loads(args[3]) == {"foo": "bar"}
        assert args[4] == "fact"

    async def test_save_memory_string_value(self, store, pool):
        await store.save_memory("t1", "key2", "plain string")
        args = pool.execute.call_args[0]
        assert args[3] == "plain string"

    async def test_save_memory_with_retention(self, store, pool):
        await store.save_memory("t1", "key3", "val", retention_days=7)
        args = pool.execute.call_args[0]
        # args: (query, tenant_id, key, value, memory_type, retention_days, expires_at)
        assert args[5] == 7  # retention_days
        assert args[6] is not None  # expires_at
        assert args[6] > datetime.now(timezone.utc)

    async def test_save_memory_no_retention(self, store, pool):
        await store.save_memory("t1", "key4", "val")
        args = pool.execute.call_args[0]
        assert args[5] is None  # retention_days
        assert args[6] is None  # expires_at

    async def test_get_memories_all_types(self, store, pool):
        now = datetime.now(timezone.utc)
        pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[
                {
                    "key": "k1",
                    "value": '{"a": 1}',
                    "memory_type": "fact",
                    "retention_days": None,
                    "expires_at": None,
                    "updated_at": now,
                },
            ]
        )
        result = await store.get_memories("t1")
        assert len(result) == 1
        assert result[0]["key"] == "k1"
        assert result[0]["value"] == {"a": 1}

    async def test_get_memories_with_type_filter(self, store, pool):
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])
        result = await store.get_memories("t1", memory_type="preference")
        assert result == []
        call_args = conn.fetch.call_args[0]
        assert "memory_type = $2" in call_args[0]

    async def test_save_project_memory(self, store, pool):
        await store.save_project_memory("t1", "proj1", "pk1", {"data": 42})
        pool.execute.assert_awaited_once()
        args = pool.execute.call_args[0]
        assert args[1] == "t1"
        assert args[2] == "proj1"
        assert args[3] == "pk1"

    async def test_get_project_memories(self, store, pool):
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])
        result = await store.get_project_memories("t1", "proj1")
        assert result == []

    async def test_cleanup_expired(self, store, pool):
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.execute = AsyncMock(side_effect=["DELETE 3", "DELETE 2"])
        deleted = await store.cleanup_expired()
        assert deleted == 5

    async def test_cleanup_expired_zero(self, store, pool):
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 0"])
        deleted = await store.cleanup_expired()
        assert deleted == 0

    async def test_memory_row_json_parsing(self):
        now = datetime.now(timezone.utc)
        row = {
            "key": "test",
            "value": '{"nested": true}',
            "memory_type": "fact",
            "retention_days": 30,
            "expires_at": now,
            "updated_at": now,
        }
        result = MemoryStore._memory_row_to_dict(row)
        assert result["value"] == {"nested": True}
        assert result["expires_at"] == now.isoformat()

    async def test_memory_row_plain_string(self):
        now = datetime.now(timezone.utc)
        row = {
            "key": "test",
            "value": "not json",
            "memory_type": "note",
            "retention_days": None,
            "expires_at": None,
            "updated_at": now,
        }
        result = MemoryStore._memory_row_to_dict(row)
        assert result["value"] == "not json"
        assert result["expires_at"] is None


class TestScopedMemoryLoader:
    @pytest.fixture
    def session_memory(self):
        sm = AsyncMock()
        sm.get_turns = AsyncMock(return_value=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        return sm

    @pytest.fixture
    def pool(self):
        return _make_pool()

    @pytest.fixture
    def loader(self, pool, session_memory):
        return ScopedMemoryLoader(pool, session_memory)

    def _make_profile(self, scopes: list[str], project_id: str | None = None):
        p = MagicMock()
        p.memory_scopes = scopes
        p.memory_project_id = project_id
        return p

    async def test_local_scope_only(self, loader, session_memory):
        profile = self._make_profile(["local"])
        bundle = await loader.load_for_session(profile, "sess1", "t1")
        assert isinstance(bundle, MemoryBundle)
        assert len(bundle.local_turns) == 2
        assert bundle.project_facts == []
        assert bundle.tenant_facts == []
        session_memory.get_turns.assert_awaited_once()

    async def test_empty_scopes(self, loader):
        profile = self._make_profile([])
        bundle = await loader.load_for_session(profile, "sess1", "t1")
        assert bundle.local_turns == []
        assert bundle.project_facts == []
        assert bundle.tenant_facts == []

    async def test_all_scopes(self, loader, pool, session_memory):
        conn = pool.acquire.return_value.__aenter__.return_value
        now = datetime.now(timezone.utc)
        tenant_rows = [
            {"key": "pref", "value": '{"color": "blue"}', "memory_type": "preference", "updated_at": now},
        ]
        project_rows = [
            {"key": "fact1", "value": '{"score": 90}', "memory_type": "fact", "updated_at": now},
        ]
        conn.fetch = AsyncMock(side_effect=[tenant_rows, project_rows])

        profile = self._make_profile(["local", "user", "project"], project_id="proj1")
        bundle = await loader.load_for_session(profile, "sess1", "t1")

        assert len(bundle.local_turns) == 2
        assert len(bundle.tenant_facts) == 1
        assert bundle.tenant_facts[0]["key"] == "pref"
        assert len(bundle.project_facts) == 1
        assert bundle.project_facts[0]["key"] == "fact1"

    async def test_project_scope_without_project_id(self, loader, session_memory):
        profile = self._make_profile(["local", "project"], project_id=None)
        bundle = await loader.load_for_session(profile, "sess1", "t1")
        assert bundle.project_facts == []
        assert len(bundle.local_turns) == 2

    async def test_memory_bundle_frozen(self):
        bundle = MemoryBundle(local_turns=[{"a": 1}], project_facts=[], tenant_facts=[])
        with pytest.raises(AttributeError):
            bundle.local_turns = []
