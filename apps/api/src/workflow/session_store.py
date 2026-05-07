"""Workflow Session Store: PostgreSQL 기반 워크플로우 세션 영속화.

기존 workflow_states 테이블을 사용하여 WorkflowSession을 저장/로드한다.
Redis 없이 PostgreSQL만 사용 (인프라 최소화 원칙).

사용법:
    store = WorkflowSessionStore(pool)
    await store.save("session-123", session)
    session = await store.load("session-123")
"""

from __future__ import annotations

import json
import time
from typing import Optional

import asyncpg

from src.observability.logging import get_logger
from src.workflow.state import WorkflowSession

logger = get_logger(__name__)


class WorkflowSessionStore:
    """PostgreSQL 기반 워크플로우 세션 저장소.

    workflow_states 테이블 사용:
        - id: session_id (PK)
        - workflow_id: 워크플로우 정의 ID
        - current_step: 현재 스텝 ID
        - state: JSONB (collected, step_history, metadata)
        - created_at, updated_at: 타임스탬프
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, session_id: str, session: WorkflowSession) -> None:
        """워크플로우 세션을 저장한다 (UPSERT).

        Args:
            session_id: 대화 세션 ID
            session: 저장할 워크플로우 세션 상태
        """
        state_json = json.dumps(
            {
                "collected": session.collected,
                "step_history": session.step_history,
                "started_at": session.started_at,
                "completed": session.completed,
                "retry_count": session.retry_count,
                "awaiting_callback": session.awaiting_callback,
                "callback_response": session.callback_response,
            },
            ensure_ascii=False,
        )

        await self._pool.execute(
            """
            INSERT INTO workflow_states (id, workflow_id, current_step, state)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                workflow_id = EXCLUDED.workflow_id,
                current_step = EXCLUDED.current_step,
                state = EXCLUDED.state,
                updated_at = NOW()
            """,
            session_id,
            session.workflow_id,
            session.current_step_id,
            state_json,
        )

        logger.debug(
            "workflow_session_saved",
            session_id=session_id,
            workflow_id=session.workflow_id,
            current_step=session.current_step_id,
        )

    async def load(self, session_id: str) -> Optional[WorkflowSession]:
        """워크플로우 세션을 로드한다.

        Args:
            session_id: 대화 세션 ID

        Returns:
            WorkflowSession 또는 None (존재하지 않을 때)
        """
        row = await self._pool.fetchrow(
            "SELECT workflow_id, current_step, state FROM workflow_states WHERE id = $1",
            session_id,
        )

        if not row:
            return None

        state = row["state"]
        if isinstance(state, str):
            state = json.loads(state)

        return WorkflowSession(
            workflow_id=row["workflow_id"],
            current_step_id=row["current_step"],
            collected=state.get("collected", {}),
            step_history=state.get("step_history", []),
            started_at=state.get("started_at", time.time()),
            completed=state.get("completed", False),
            retry_count=state.get("retry_count", 0),
            awaiting_callback=state.get("awaiting_callback", False),
            callback_response=state.get("callback_response", {}),
        )

    async def delete(self, session_id: str) -> None:
        """워크플로우 세션을 삭제한다.

        Args:
            session_id: 대화 세션 ID
        """
        result = await self._pool.execute(
            "DELETE FROM workflow_states WHERE id = $1",
            session_id,
        )
        rows_affected = int(result.split()[-1])
        if rows_affected > 0:
            logger.debug("workflow_session_deleted", session_id=session_id)

    async def cleanup_expired(self, ttl_seconds: int = 86400) -> int:
        """TTL 초과 세션을 삭제한다.

        완료되었거나 오래된 세션을 정리한다.
        completed=true인 세션과 updated_at이 TTL을 초과한 세션 모두 삭제.

        Args:
            ttl_seconds: 세션 유효 기간 (기본 24시간)

        Returns:
            삭제된 세션 수
        """
        result = await self._pool.execute(
            """
            DELETE FROM workflow_states
            WHERE updated_at < NOW() - make_interval(secs => $1::double precision)
            """,
            float(ttl_seconds),
        )
        count = int(result.split()[-1])
        if count > 0:
            logger.info(
                "workflow_sessions_cleaned",
                deleted_count=count,
                ttl_seconds=ttl_seconds,
            )
        return count

    async def exists(self, session_id: str) -> bool:
        """세션이 존재하는지 확인한다.

        Args:
            session_id: 대화 세션 ID

        Returns:
            존재 여부
        """
        row = await self._pool.fetchval(
            "SELECT 1 FROM workflow_states WHERE id = $1",
            session_id,
        )
        return row is not None
