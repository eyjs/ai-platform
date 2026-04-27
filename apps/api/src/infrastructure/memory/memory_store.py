"""MemoryStore: Memory System 통합 서비스 레이어.

Memory System의 읽기/쓰기/만료 정리를 통합하는 서비스.
tenant_memory와 project_memory 테이블에 대한 CRUD + 만료 정리.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


class MemoryStore:
    """Memory System 통합 서비스.

    - tenant_memory, project_memory 테이블 CRUD
    - 자동 만료 정리 (백그라운드 태스크)
    - JSON 값 자동 직렬화/역직렬화
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._cleanup_task: Optional[asyncio.Task] = None
        self._cleanup_running = False

    async def save_memory(
        self,
        tenant_id: str,
        key: str,
        value: dict | str,
        memory_type: str = "fact",
        retention_days: Optional[int] = None,
    ) -> None:
        """tenant_memory에 메모리 저장 (upsert).

        Args:
            tenant_id: 테넌트 ID
            key: 메모리 키
            value: 메모리 값 (dict는 JSON으로 자동 변환)
            memory_type: 메모리 타입 (fact, preference, history 등)
            retention_days: 보존 기간 (일). None이면 영구 보존
        """
        json_value = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
        expires_at = None
        if retention_days is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(days=retention_days)

        await self._pool.execute(
            """
            INSERT INTO tenant_memory (tenant_id, key, value, memory_type, retention_days, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (tenant_id, key) DO UPDATE SET
                value = $3,
                memory_type = $4,
                retention_days = $5,
                expires_at = $6,
                updated_at = NOW()
            """,
            tenant_id, key, json_value, memory_type, retention_days, expires_at,
        )

    async def get_memories(
        self,
        tenant_id: str,
        limit: int = 50,
        memory_type: Optional[str] = None,
    ) -> list[dict]:
        """tenant_memory에서 메모리 조회.

        Args:
            tenant_id: 테넌트 ID
            limit: 최대 조회 개수
            memory_type: 메모리 타입 필터. None이면 모든 타입

        Returns:
            메모리 항목 목록 (최근 순)
        """
        query = """
            SELECT key, value, memory_type, retention_days, expires_at, updated_at
            FROM tenant_memory
            WHERE tenant_id = $1
        """
        params = [tenant_id]

        if memory_type:
            query += " AND memory_type = $2"
            params.append(memory_type)
            query += " ORDER BY updated_at DESC LIMIT $3"
            params.append(limit)
        else:
            query += " ORDER BY updated_at DESC LIMIT $2"
            params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [self._memory_row_to_dict(row) for row in rows]

    async def save_project_memory(
        self,
        tenant_id: str,
        project_id: str,
        key: str,
        value: dict | str,
        memory_type: str = "fact",
        retention_days: Optional[int] = None,
    ) -> None:
        """project_memory에 프로젝트별 메모리 저장 (upsert).

        Args:
            tenant_id: 테넌트 ID
            project_id: 프로젝트 ID
            key: 메모리 키
            value: 메모리 값 (dict는 JSON으로 자동 변환)
            memory_type: 메모리 타입
            retention_days: 보존 기간 (일). None이면 영구 보존
        """
        json_value = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
        expires_at = None
        if retention_days is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(days=retention_days)

        await self._pool.execute(
            """
            INSERT INTO project_memory (tenant_id, project_id, key, value, memory_type, retention_days, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (tenant_id, project_id, key) DO UPDATE SET
                value = $4,
                memory_type = $5,
                retention_days = $6,
                expires_at = $7,
                updated_at = NOW()
            """,
            tenant_id, project_id, key, json_value, memory_type, retention_days, expires_at,
        )

    async def get_project_memories(
        self,
        tenant_id: str,
        project_id: str,
        limit: int = 50,
        memory_type: Optional[str] = None,
    ) -> list[dict]:
        """project_memory에서 프로젝트별 메모리 조회.

        Args:
            tenant_id: 테넌트 ID
            project_id: 프로젝트 ID
            limit: 최대 조회 개수
            memory_type: 메모리 타입 필터. None이면 모든 타입

        Returns:
            메모리 항목 목록 (최근 순)
        """
        query = """
            SELECT key, value, memory_type, retention_days, expires_at, updated_at
            FROM project_memory
            WHERE tenant_id = $1 AND project_id = $2
        """
        params = [tenant_id, project_id]

        if memory_type:
            query += " AND memory_type = $3"
            params.append(memory_type)
            query += " ORDER BY updated_at DESC LIMIT $4"
            params.append(limit)
        else:
            query += " ORDER BY updated_at DESC LIMIT $3"
            params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [self._memory_row_to_dict(row) for row in rows]

    async def cleanup_expired(self) -> int:
        """만료된 메모리 항목 정리.

        Returns:
            삭제된 행 수
        """
        async with self._pool.acquire() as conn:
            # tenant_memory 정리
            result1 = await conn.execute(
                "DELETE FROM tenant_memory WHERE expires_at IS NOT NULL AND expires_at < NOW()"
            )
            tenant_deleted = int(result1.split()[-1])

            # project_memory 정리
            result2 = await conn.execute(
                "DELETE FROM project_memory WHERE expires_at IS NOT NULL AND expires_at < NOW()"
            )
            project_deleted = int(result2.split()[-1])

        total_deleted = tenant_deleted + project_deleted
        if total_deleted > 0:
            logger.info(
                "memory_cleanup_completed",
                extra={
                    "tenant_deleted": tenant_deleted,
                    "project_deleted": project_deleted,
                    "total_deleted": total_deleted,
                },
            )
        return total_deleted

    async def start_cleanup_sweeper(self, interval_seconds: int = 60) -> None:
        """만료 정리 백그라운드 태스크 시작.

        Args:
            interval_seconds: 정리 주기 (기본 60초)
        """
        if self._cleanup_running:
            logger.warning("cleanup_sweeper already running")
            return

        self._cleanup_running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(interval_seconds))
        logger.info("memory_cleanup_sweeper_started", extra={"interval_seconds": interval_seconds})

    async def stop_cleanup_sweeper(self) -> None:
        """만료 정리 백그라운드 태스크 중지."""
        if not self._cleanup_running:
            return

        self._cleanup_running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        logger.info("memory_cleanup_sweeper_stopped")

    async def _cleanup_loop(self, interval_seconds: int) -> None:
        """만료 정리 루프."""
        while self._cleanup_running:
            try:
                await self.cleanup_expired()
            except Exception as e:
                logger.error("memory_cleanup_error", extra={"error": str(e)}, exc_info=True)

            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break

    @staticmethod
    def _memory_row_to_dict(row) -> dict:
        """메모리 행을 dict로 변환.

        JSON 문자열은 자동으로 파싱한다.
        """
        value = row["value"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                # JSON이 아닌 일반 문자열은 그대로 유지
                pass

        return {
            "key": row["key"],
            "value": value,
            "memory_type": row["memory_type"],
            "retention_days": row["retention_days"],
            "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }