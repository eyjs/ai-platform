"""WorkflowSessionStore 테스트.

asyncpg.Pool을 AsyncMock으로 대체하여 DB 없이 단위 테스트한다.
save/load/delete/cleanup/exists의 SQL 호출 및 데이터 변환을 검증한다.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.workflow.session_store import WorkflowSessionStore
from src.workflow.state import WorkflowSession


def _make_pool() -> MagicMock:
    """asyncpg.Pool을 모킹한다."""
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.fetchval = AsyncMock()
    return pool


class TestWorkflowSessionStoreSave:

    async def test_save_calls_execute_with_upsert(self):
        """save()가 올바른 UPSERT SQL을 호출한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)

        session = WorkflowSession(
            workflow_id="wf_test",
            current_step_id="step_1",
            collected={"name": "홍길동"},
            step_history=["step_0"],
            completed=False,
            retry_count=1,
        )

        await store.save("sess_123", session)

        pool.execute.assert_called_once()
        call_args = pool.execute.call_args
        sql = call_args[0][0]
        assert "INSERT INTO workflow_states" in sql
        assert "ON CONFLICT (id) DO UPDATE" in sql

        # 위치 인자 확인
        args = call_args[0]
        assert args[1] == "sess_123"  # session_id
        assert args[2] == "wf_test"  # workflow_id
        assert args[3] == "step_1"  # current_step

        # state JSON 검증
        state = json.loads(args[4])
        assert state["collected"] == {"name": "홍길동"}
        assert state["step_history"] == ["step_0"]
        assert state["completed"] is False
        assert state["retry_count"] == 1

    async def test_save_includes_callback_fields(self):
        """save()가 awaiting_callback, callback_response를 포함한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)

        session = WorkflowSession(
            workflow_id="wf_test",
            current_step_id="step_2",
            awaiting_callback=True,
            callback_response={"contract_id": "C-001"},
        )

        await store.save("sess_456", session)

        state = json.loads(pool.execute.call_args[0][4])
        assert state["awaiting_callback"] is True
        assert state["callback_response"] == {"contract_id": "C-001"}


class TestWorkflowSessionStoreLoad:

    async def test_load_returns_session(self):
        """load()가 DB 행을 WorkflowSession으로 변환한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)

        pool.fetchrow.return_value = {
            "workflow_id": "wf_insurance",
            "current_step": "ask_name",
            "state": json.dumps({
                "collected": {"type": "자동차보험"},
                "step_history": ["welcome", "select_type"],
                "started_at": 1700000000.0,
                "completed": False,
                "retry_count": 0,
                "awaiting_callback": False,
                "callback_response": {},
            }),
        }

        session = await store.load("sess_789")

        assert session is not None
        assert session.workflow_id == "wf_insurance"
        assert session.current_step_id == "ask_name"
        assert session.collected == {"type": "자동차보험"}
        assert session.step_history == ["welcome", "select_type"]
        assert session.started_at == 1700000000.0
        assert session.completed is False
        assert session.retry_count == 0

    async def test_load_returns_none_if_not_found(self):
        """load()가 없는 세션에 대해 None을 반환한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)
        pool.fetchrow.return_value = None

        session = await store.load("nonexistent")
        assert session is None

    async def test_load_handles_parsed_json(self):
        """state가 이미 파싱된 dict인 경우도 처리한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)

        # asyncpg는 JSONB를 dict로 반환할 수도 있다
        pool.fetchrow.return_value = {
            "workflow_id": "wf_test",
            "current_step": "step_1",
            "state": {
                "collected": {},
                "step_history": [],
                "started_at": 1700000000.0,
                "completed": True,
                "retry_count": 0,
                "awaiting_callback": False,
                "callback_response": {},
            },
        }

        session = await store.load("sess_dict")
        assert session is not None
        assert session.completed is True

    async def test_load_handles_missing_optional_fields(self):
        """state에 선택적 필드가 없어도 기본값으로 처리한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)

        pool.fetchrow.return_value = {
            "workflow_id": "wf_test",
            "current_step": "step_1",
            "state": json.dumps({
                "collected": {"x": "y"},
                # step_history, started_at 등 누락
            }),
        }

        session = await store.load("sess_minimal")
        assert session is not None
        assert session.step_history == []
        assert session.completed is False
        assert session.retry_count == 0
        assert session.awaiting_callback is False


class TestWorkflowSessionStoreDelete:

    async def test_delete_calls_execute(self):
        """delete()가 DELETE SQL을 호출한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)
        pool.execute.return_value = "DELETE 1"

        await store.delete("sess_123")

        pool.execute.assert_called_once()
        sql = pool.execute.call_args[0][0]
        assert "DELETE FROM workflow_states" in sql
        assert pool.execute.call_args[0][1] == "sess_123"


class TestWorkflowSessionStoreCleanup:

    async def test_cleanup_expired_returns_count(self):
        """cleanup_expired()가 삭제된 행 수를 반환한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)
        pool.execute.return_value = "DELETE 5"

        count = await store.cleanup_expired(ttl_seconds=3600)
        assert count == 5

    async def test_cleanup_with_zero_rows(self):
        """삭제된 행이 없을 때 0을 반환한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)
        pool.execute.return_value = "DELETE 0"

        count = await store.cleanup_expired()
        assert count == 0


class TestWorkflowSessionStoreExists:

    async def test_exists_returns_true(self):
        """exists()가 존재하는 세션에 대해 True를 반환한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)
        pool.fetchval.return_value = 1

        assert await store.exists("sess_123") is True

    async def test_exists_returns_false(self):
        """exists()가 없는 세션에 대해 False를 반환한다."""
        pool = _make_pool()
        store = WorkflowSessionStore(pool)
        pool.fetchval.return_value = None

        assert await store.exists("nonexistent") is False
