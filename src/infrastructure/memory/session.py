"""PostgreSQL 기반 대화 세션 메모리.

Redis 대체 — conversation_sessions 테이블 사용.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import asyncpg

logger = logging.getLogger(__name__)


class SessionMemory:
    """PostgreSQL 기반 대화 세션 메모리."""

    def __init__(self, pool: asyncpg.Pool, default_ttl_seconds: int = 3600):
        self._pool = pool
        self._default_ttl = default_ttl_seconds

    async def create_session(
        self,
        session_id: str,
        profile_id: str,
        user_id: str = "",
        ttl_seconds: Optional[int] = None,
    ) -> None:
        ttl = ttl_seconds or self._default_ttl
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        await self._pool.execute(
            """
            INSERT INTO conversation_sessions (id, profile_id, user_id, expires_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO UPDATE SET updated_at = NOW(), expires_at = $4
            """,
            session_id, profile_id, user_id, expires_at,
        )

    async def add_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """대화 턴 추가. 세션이 없으면 경고 로그를 남긴다."""
        turn = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
        result = await self._pool.execute(
            """
            UPDATE conversation_sessions
            SET turns = turns || $2::jsonb,
                updated_at = NOW()
            WHERE id = $1
            """,
            session_id, json.dumps([turn], ensure_ascii=False),
        )
        rows_affected = int(result.split()[-1])
        if rows_affected == 0:
            logger.warning("add_turn: session not found, turn dropped", extra={"session_id": session_id})

    async def get_turns(
        self,
        session_id: str,
        max_turns: int = 10,
    ) -> List[dict]:
        """최근 N턴 조회."""
        row = await self._pool.fetchrow(
            "SELECT turns FROM conversation_sessions WHERE id = $1",
            session_id,
        )
        if not row or not row["turns"]:
            return []
        turns = json.loads(row["turns"]) if isinstance(row["turns"], str) else row["turns"]
        return turns[-max_turns:]

    async def get_session(self, session_id: str) -> Optional[dict]:
        row = await self._pool.fetchrow(
            "SELECT * FROM conversation_sessions WHERE id = $1",
            session_id,
        )
        if not row:
            return None
        return dict(row)

    async def update_current_profile(self, session_id: str, profile_id: str) -> None:
        """세션의 현재 프로필을 업데이트한다."""
        await self._pool.execute(
            """
            UPDATE conversation_sessions
            SET current_profile_id = $2, updated_at = NOW()
            WHERE id = $1
            """,
            session_id, profile_id,
        )

    async def save_orchestrator_metadata(self, session_id: str, metadata: dict) -> None:
        """오케스트레이터 메타데이터를 저장한다."""
        await self._pool.execute(
            """
            UPDATE conversation_sessions
            SET orchestrator_meta = $2::jsonb, updated_at = NOW()
            WHERE id = $1
            """,
            session_id, json.dumps(metadata, ensure_ascii=False),
        )

    async def get_orchestrator_metadata(self, session_id: str) -> dict:
        """오케스트레이터 메타데이터를 조회한다."""
        row = await self._pool.fetchrow(
            "SELECT orchestrator_meta FROM conversation_sessions WHERE id = $1",
            session_id,
        )
        if not row or not row["orchestrator_meta"]:
            return {}
        meta = row["orchestrator_meta"]
        if isinstance(meta, str):
            return json.loads(meta)
        return dict(meta)

    async def cleanup_expired(self) -> int:
        """만료된 세션 삭제."""
        result = await self._pool.execute(
            "DELETE FROM conversation_sessions WHERE expires_at < NOW()"
        )
        count = int(result.split()[-1])
        if count > 0:
            logger.info("Cleaned up %d expired sessions", count)
        return count
