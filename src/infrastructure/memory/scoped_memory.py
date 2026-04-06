"""Scoped Memory Loader: 3-스코프 메모리 병렬 조회.

local (세션 대화) + user (테넌트 전역) + project (상품별) 스코프를
asyncio.gather()로 병렬 조회하여 MemoryBundle로 묶어 반환한다.

기존 SessionMemory를 변경하지 않고, local 스코프를 위임 호출한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import asyncpg

from src.domain.agent_profile import AgentProfile
from src.infrastructure.memory.session import SessionMemory

logger = logging.getLogger(__name__)

SCOPE_QUERY_TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True)
class MemoryBundle:
    """3-스코프 메모리 조회 결과 번들."""

    local_turns: list[dict] = field(default_factory=list)
    project_facts: list[dict] = field(default_factory=list)
    tenant_facts: list[dict] = field(default_factory=list)


class ScopedMemoryLoader:
    """Profile의 memory_scopes에 따라 병렬로 메모리를 조회한다."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        session_memory: SessionMemory,
    ):
        self._pool = pool
        self._session_memory = session_memory

    async def load_for_session(
        self,
        profile: AgentProfile,
        session_id: str,
        tenant_id: str,
    ) -> MemoryBundle:
        """profile.memory_scopes에 따라 병렬 조회.

        - "local" in scopes -> SessionMemory.get_turns()
        - "user" in scopes -> tenant_memory 테이블 조회
        - "project" in scopes -> project_memory 테이블 조회
        - 미포함 스코프 -> 빈 리스트

        각 스코프 조회에 SCOPE_QUERY_TIMEOUT_SECONDS 타임아웃 적용.
        조회 실패 시 빈 리스트로 Fallback.
        """
        scopes = set(profile.memory_scopes)
        t_start = time.time()

        tasks: dict[str, asyncio.Task] = {}

        if "local" in scopes:
            tasks["local"] = asyncio.create_task(
                self._load_local(session_id),
            )
        if "user" in scopes:
            tasks["user"] = asyncio.create_task(
                self._load_tenant(tenant_id),
            )
        if "project" in scopes and profile.memory_project_id:
            tasks["project"] = asyncio.create_task(
                self._load_project(tenant_id, profile.memory_project_id),
            )

        results: dict[str, list[dict]] = {}
        if tasks:
            done, _ = await asyncio.wait(
                tasks.values(),
                timeout=SCOPE_QUERY_TIMEOUT_SECONDS,
            )
            for scope_name, task in tasks.items():
                if task in done:
                    try:
                        results[scope_name] = task.result()
                    except Exception as e:
                        logger.warning(
                            "scoped_memory_load_failed",
                            extra={
                                "scope": scope_name,
                                "tenant_id": tenant_id,
                                "error": str(e),
                            },
                        )
                        results[scope_name] = []
                else:
                    logger.warning(
                        "scoped_memory_timeout",
                        extra={
                            "scope": scope_name,
                            "tenant_id": tenant_id,
                            "timeout_seconds": SCOPE_QUERY_TIMEOUT_SECONDS,
                        },
                    )
                    task.cancel()
                    results[scope_name] = []

        elapsed_ms = (time.time() - t_start) * 1000
        logger.info(
            "scoped_memory_loaded",
            extra={
                "scopes": list(scopes),
                "local_count": len(results.get("local", [])),
                "user_count": len(results.get("user", [])),
                "project_count": len(results.get("project", [])),
                "latency_ms": round(elapsed_ms, 1),
            },
        )

        return MemoryBundle(
            local_turns=results.get("local", []),
            project_facts=results.get("project", []),
            tenant_facts=results.get("user", []),
        )

    async def _load_local(self, session_id: str) -> list[dict]:
        """local 스코프: 기존 SessionMemory 위임."""
        return await self._session_memory.get_turns(session_id)

    async def _load_tenant(self, tenant_id: str) -> list[dict]:
        """user 스코프: tenant_memory 테이블 조회."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT key, value, memory_type, updated_at
                FROM tenant_memory
                WHERE tenant_id = $1
                ORDER BY updated_at DESC
                LIMIT 50
                """,
                tenant_id,
            )
        return [self._fact_row_to_dict(row) for row in rows]

    async def _load_project(
        self, tenant_id: str, project_id: str,
    ) -> list[dict]:
        """project 스코프: project_memory 테이블 조회."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT key, value, memory_type, updated_at
                FROM project_memory
                WHERE tenant_id = $1 AND project_id = $2
                ORDER BY updated_at DESC
                LIMIT 50
                """,
                tenant_id, project_id,
            )
        return [self._fact_row_to_dict(row) for row in rows]

    @staticmethod
    def _fact_row_to_dict(row) -> dict:
        """팩트 행을 dict로 변환."""
        value = row["value"]
        if isinstance(value, str):
            value = json.loads(value)
        return {
            "key": row["key"],
            "value": value,
            "memory_type": row["memory_type"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else "",
        }
