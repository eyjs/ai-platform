"""Feedback Service (PostgreSQL 기반).

- POST /api/feedback 저장 (upsert by (user_id, response_id))
- Admin 조회 (JOIN api_request_logs)
- 30일 auto-purge sweeper (response_cache sweeper 패턴 복제)

Contract: .pipeline/contracts/feedback-dto.md
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy import text

from .feedback_models import (
    AdminFeedbackItem,
    AdminFeedbackPage,
    FeedbackRequest,
    FeedbackResponse,
)

logger = logging.getLogger(__name__)


class FeedbackService:
    """피드백 저장/조회 + 30일 purge sweeper."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        retention_days: int = 30,
    ):
        self._session_factory = session_factory
        self._retention_days = retention_days
        self._sweeper_task: Optional[asyncio.Task[None]] = None
        self._sweeper_stopped = True

    # ---- submit ----

    async def submit(
        self,
        user_id: str,
        req: FeedbackRequest,
    ) -> FeedbackResponse:
        """피드백 insert (또는 upsert by (user_id, response_id))."""
        async with self._session_factory() as session:
            row = (await session.execute(
                text(
                    """
                    INSERT INTO response_feedback
                        (response_id, score, comment, user_id)
                    VALUES (:rid, :score, :comment, :uid)
                    ON CONFLICT (user_id, response_id) DO UPDATE
                        SET score = EXCLUDED.score,
                            comment = EXCLUDED.comment,
                            created_at = NOW()
                    RETURNING id, response_id, score, created_at,
                              (xmax <> 0) AS upserted
                    """
                ),
                {
                    "rid": str(req.response_id),
                    "score": req.score,
                    "comment": req.comment,
                    "uid": user_id,
                },
            )).mappings().first()
            await session.commit()

        if row is None:
            # 비상 케이스 — INSERT/UPSERT 가 아무 것도 반환하지 않은 경우
            raise RuntimeError("feedback upsert returned no row")

        return FeedbackResponse(
            id=str(row["id"]),
            response_id=str(row["response_id"]),
            score=int(row["score"]),
            created_at=row["created_at"],
            upserted=bool(row["upserted"]),
        )

    # ---- admin list ----

    async def list_for_admin(
        self,
        limit: int = 50,
        offset: int = 0,
        only_negative: bool = False,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> AdminFeedbackPage:
        """JOIN api_request_logs + 필터."""
        if limit < 1:
            limit = 1
        if limit > 200:
            limit = 200
        if offset < 0:
            offset = 0

        # 공통 WHERE
        where_parts: list[str] = []
        params: dict[str, Any] = {}
        if only_negative:
            where_parts.append("f.score = -1")
        if date_from is not None:
            where_parts.append("f.created_at >= :date_from")
            params["date_from"] = date_from
        if date_to is not None:
            where_parts.append("f.created_at < :date_to")
            params["date_to"] = date_to
        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        list_sql = text(
            f"""
            SELECT
                f.id,
                f.response_id,
                f.score,
                f.comment,
                f.created_at,
                f.user_id,
                l.profile_id,
                l.faithfulness_score,
                l.request_preview  AS question_preview,
                l.response_preview AS answer_preview,
                l.ts               AS response_ts
            FROM response_feedback f
            LEFT JOIN api_request_logs l ON l.response_id = f.response_id
            {where_clause}
            ORDER BY f.created_at DESC
            LIMIT :limit OFFSET :offset
            """
        )
        count_sql = text(
            f"SELECT COUNT(*) AS c FROM response_feedback f {where_clause}"
        )

        async with self._session_factory() as session:
            list_rows = (await session.execute(
                list_sql,
                {**params, "limit": limit, "offset": offset},
            )).mappings().all()
            total_row = (await session.execute(count_sql, params)).mappings().first()

        items = [
            AdminFeedbackItem(
                id=str(r["id"]),
                response_id=str(r["response_id"]),
                score=int(r["score"]),
                comment=r["comment"],
                created_at=r["created_at"],
                user_id=str(r["user_id"]),
                profile_id=r["profile_id"],
                faithfulness_score=(
                    float(r["faithfulness_score"])
                    if r["faithfulness_score"] is not None
                    else None
                ),
                question_preview=r["question_preview"],
                answer_preview=r["answer_preview"],
                response_ts=r["response_ts"],
            )
            for r in list_rows
        ]

        return AdminFeedbackPage(
            items=items,
            total=int(total_row["c"]) if total_row else 0,
            limit=limit,
            offset=offset,
        )

    # ---- sweeper (30일 auto-purge) ----

    async def start_sweeper(self, interval_seconds: int = 3600) -> None:
        if self._sweeper_task is not None:
            return
        self._sweeper_stopped = False
        self._sweeper_task = asyncio.create_task(
            self._sweep_loop(interval_seconds),
            name="feedback.sweeper",
        )
        logger.info(
            "feedback.sweeper.started interval=%ds retention=%dd",
            interval_seconds,
            self._retention_days,
        )

    async def stop_sweeper(self) -> None:
        self._sweeper_stopped = True
        if self._sweeper_task is not None:
            try:
                await asyncio.wait_for(self._sweeper_task, timeout=3.0)
            except asyncio.TimeoutError:
                self._sweeper_task.cancel()
            self._sweeper_task = None
        logger.info("feedback.sweeper.stopped")

    async def purge_once(self) -> int:
        """한 번만 실행 (테스트 및 수동 트리거용)."""
        # INTERVAL 은 파라미터 바인딩이 까다로워 format 으로 주입 (retention_days 는 int 라 안전)
        days = int(self._retention_days)
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    text(
                        f"DELETE FROM response_feedback "
                        f"WHERE created_at < NOW() - INTERVAL '{days} days'"
                    ),
                )
                await session.commit()
                removed = int(result.rowcount or 0)
                if removed > 0:
                    logger.info("feedback.sweep.removed count=%d", removed)
                return removed
        except Exception as e:
            logger.warning("feedback.sweep.error error=%s", e)
            return 0

    async def _sweep_loop(self, interval: int) -> None:
        while not self._sweeper_stopped:
            await self.purge_once()
            # interval 동안 sleep, 중단 체크
            try:
                for _ in range(max(1, interval)):
                    if self._sweeper_stopped:
                        return
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                return
