"""FeedbackService 단위 테스트.

- submit: upsert 경로
- list_for_admin: only_negative / date range
- sweeper: purge_once

실제 DB 없이 FakeSession 으로 SQL 파라미터를 검증한다 (단위 테스트 수준).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from src.services.feedback_models import FeedbackRequest
from src.services.feedback_service import FeedbackService


# ---- FakeSession / Factory ----


class FakeRow(dict):
    """asyncpg-like mapping row."""

    def __getattr__(self, key: str):
        return self.get(key)


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, responses: list):
        """responses: 매 execute 호출마다 반환할 FakeResult 리스트."""
        self._responses = list(responses)
        self.captured: list[tuple[str, dict | list]] = []
        self.committed = False

    async def execute(self, sql, params=None):
        self.captured.append((str(sql), params))
        if self._responses:
            return self._responses.pop(0)
        return FakeResult()

    async def commit(self):
        self.committed = True


def _make_factory(sessions: list):
    """매 호출마다 다음 FakeSession 을 반환하는 async context factory."""
    iterator = iter(sessions)

    @asynccontextmanager
    async def factory():
        s = next(iterator)
        yield s

    return factory


# ---- Tests ----


@pytest.mark.asyncio
async def test_submit_inserts_row_and_returns_response():
    """S1 검증: FeedbackService.submit 이 INSERT RETURNING 결과를 DTO 로 반환한다."""
    rid = uuid4()
    fid = uuid4()
    now = datetime.now(timezone.utc)
    insert_result = FakeResult(
        rows=[FakeRow(id=fid, response_id=rid, score=1, created_at=now, upserted=False)]
    )
    session = FakeSession(responses=[insert_result])
    svc = FeedbackService(_make_factory([session]))

    req = FeedbackRequest(response_id=rid, score=1, comment="good")
    resp = await svc.submit(user_id=str(uuid4()), req=req)

    assert resp.score == 1
    assert UUID(resp.response_id) == rid
    assert resp.upserted is False
    # SQL 에 ON CONFLICT DO UPDATE 포함 (upsert 정책)
    sql_text = session.captured[0][0].upper()
    assert "ON CONFLICT" in sql_text
    assert session.committed is True


@pytest.mark.asyncio
async def test_submit_upsert_flag_when_conflict():
    """P1 중복 방지: (user_id, response_id) 충돌 시 upserted=True 반환."""
    rid = uuid4()
    fid = uuid4()
    now = datetime.now(timezone.utc)
    insert_result = FakeResult(
        rows=[FakeRow(id=fid, response_id=rid, score=-1, created_at=now, upserted=True)]
    )
    session = FakeSession(responses=[insert_result])
    svc = FeedbackService(_make_factory([session]))

    resp = await svc.submit(
        user_id=str(uuid4()),
        req=FeedbackRequest(response_id=rid, score=-1),
    )
    assert resp.upserted is True


@pytest.mark.asyncio
async def test_list_only_negative_adds_score_predicate():
    """S4 일부: only_negative=True 일 때 WHERE 에 score=-1 포함."""
    list_result = FakeResult(rows=[])
    count_result = FakeResult(rows=[FakeRow(c=0)])
    session = FakeSession(responses=[list_result, count_result])
    svc = FeedbackService(_make_factory([session]))

    page = await svc.list_for_admin(only_negative=True)

    assert page.total == 0
    # 첫번째 execute 가 list SQL
    list_sql = session.captured[0][0]
    assert "f.score = -1" in list_sql
    assert "ORDER BY f.created_at DESC" in list_sql


@pytest.mark.asyncio
async def test_list_date_range_binds_params():
    """date_from, date_to 가 where 와 바인딩 params 에 들어간다."""
    list_result = FakeResult(rows=[])
    count_result = FakeResult(rows=[FakeRow(c=0)])
    session = FakeSession(responses=[list_result, count_result])
    svc = FeedbackService(_make_factory([session]))

    df = datetime(2026, 4, 1, tzinfo=timezone.utc)
    dt_ = datetime(2026, 4, 20, tzinfo=timezone.utc)
    await svc.list_for_admin(date_from=df, date_to=dt_)

    list_sql = session.captured[0][0]
    list_params = session.captured[0][1]
    assert ":date_from" in list_sql
    assert ":date_to" in list_sql
    assert list_params["date_from"] == df
    assert list_params["date_to"] == dt_


@pytest.mark.asyncio
async def test_list_limit_clamped_to_max_200():
    """limit > 200 은 200 으로 클램프."""
    list_result = FakeResult(rows=[])
    count_result = FakeResult(rows=[FakeRow(c=0)])
    session = FakeSession(responses=[list_result, count_result])
    svc = FeedbackService(_make_factory([session]))

    page = await svc.list_for_admin(limit=9999)
    assert page.limit == 200


@pytest.mark.asyncio
async def test_purge_once_issues_delete_with_interval():
    """S6 검증: purge_once() 가 30일 interval 로 DELETE 실행."""
    delete_result = FakeResult(rowcount=3)
    session = FakeSession(responses=[delete_result])
    svc = FeedbackService(_make_factory([session]), retention_days=30)

    removed = await svc.purge_once()

    assert removed == 3
    sql_text = session.captured[0][0]
    assert "DELETE FROM response_feedback" in sql_text
    assert "INTERVAL '30 days'" in sql_text
    assert session.committed is True


@pytest.mark.asyncio
async def test_sweeper_start_stop_is_idempotent():
    """start_sweeper / stop_sweeper 는 여러 번 호출해도 안전."""
    # 많은 delete response 를 주입해도 stop 시 종료
    sessions = [FakeSession(responses=[FakeResult(rowcount=0)]) for _ in range(5)]
    svc = FeedbackService(_make_factory(sessions), retention_days=30)

    await svc.start_sweeper(interval_seconds=1)
    await svc.start_sweeper(interval_seconds=1)  # 두번째는 no-op
    await asyncio.sleep(0.05)
    await svc.stop_sweeper()
    await svc.stop_sweeper()  # 두번째는 no-op
