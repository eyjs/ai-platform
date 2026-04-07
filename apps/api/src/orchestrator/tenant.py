"""TenantService: 멀티테넌트 격리 + 프로필 필터링."""

from __future__ import annotations

from typing import Optional

import asyncpg

from src.observability.logging import get_logger
from src.orchestrator.models import TenantConfig

logger = get_logger(__name__)

_UNSET = object()  # sentinel: "인자 미전달" vs "None으로 설정"


class TenantService:
    """테넌트 관리: CRUD + 프로필 매핑."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_allowed_profiles(self, tenant_id: str | None) -> list[str]:
        """테넌트에 할당된 프로필 ID 목록을 반환한다.

        tenant_id가 None이면 빈 리스트 (전체 접근).
        """
        if not tenant_id:
            return []
        rows = await self._pool.fetch(
            "SELECT profile_id FROM tenant_profiles WHERE tenant_id = $1",
            tenant_id,
        )
        return [r["profile_id"] for r in rows]

    async def get(self, tenant_id: str) -> Optional[TenantConfig]:
        """테넌트 조회."""
        row = await self._pool.fetchrow(
            "SELECT id, name, orchestrator_enabled, default_chatbot_id, is_active "
            "FROM tenants WHERE id = $1",
            tenant_id,
        )
        if not row:
            return None
        return TenantConfig(
            id=row["id"],
            name=row["name"],
            orchestrator_enabled=row["orchestrator_enabled"],
            default_chatbot_id=row["default_chatbot_id"],
            is_active=row["is_active"],
        )

    async def list_all(self) -> list[TenantConfig]:
        """모든 활성 테넌트 목록."""
        rows = await self._pool.fetch(
            "SELECT id, name, orchestrator_enabled, default_chatbot_id, is_active "
            "FROM tenants WHERE is_active = TRUE ORDER BY name"
        )
        return [
            TenantConfig(
                id=r["id"],
                name=r["name"],
                orchestrator_enabled=r["orchestrator_enabled"],
                default_chatbot_id=r["default_chatbot_id"],
                is_active=r["is_active"],
            )
            for r in rows
        ]

    async def create(
        self,
        tenant_id: str,
        name: str,
        description: str = "",
        orchestrator_enabled: bool = True,
        default_chatbot_id: str | None = None,
    ) -> TenantConfig:
        """테넌트 생성."""
        await self._pool.execute(
            """
            INSERT INTO tenants (id, name, description, orchestrator_enabled, default_chatbot_id)
            VALUES ($1, $2, $3, $4, $5)
            """,
            tenant_id, name, description, orchestrator_enabled, default_chatbot_id,
        )
        logger.info("tenant_created", tenant_id=tenant_id, name=name)
        return TenantConfig(
            id=tenant_id,
            name=name,
            orchestrator_enabled=orchestrator_enabled,
            default_chatbot_id=default_chatbot_id,
        )

    async def update(
        self,
        tenant_id: str,
        name: str | None = None,
        description: str | None = None,
        orchestrator_enabled: bool | None = None,
        default_chatbot_id: str | None | object = _UNSET,
    ) -> bool:
        """테넌트 업데이트 (부분 수정).

        default_chatbot_id에 None을 전달하면 값을 지울 수 있다.
        """
        sets = []
        params = []
        idx = 1

        if name is not None:
            sets.append(f"name = ${idx}")
            params.append(name)
            idx += 1
        if description is not None:
            sets.append(f"description = ${idx}")
            params.append(description)
            idx += 1
        if orchestrator_enabled is not None:
            sets.append(f"orchestrator_enabled = ${idx}")
            params.append(orchestrator_enabled)
            idx += 1
        if default_chatbot_id is not _UNSET:
            sets.append(f"default_chatbot_id = ${idx}")
            params.append(default_chatbot_id)
            idx += 1

        if not sets:
            return False

        sets.append("updated_at = NOW()")
        params.append(tenant_id)

        query = f"UPDATE tenants SET {', '.join(sets)} WHERE id = ${idx}"
        result = await self._pool.execute(query, *params)
        return int(result.split()[-1]) > 0

    async def deactivate(self, tenant_id: str) -> bool:
        """테넌트 비활성화 (soft delete)."""
        result = await self._pool.execute(
            "UPDATE tenants SET is_active = FALSE, updated_at = NOW() WHERE id = $1",
            tenant_id,
        )
        return int(result.split()[-1]) > 0

    async def set_profiles(self, tenant_id: str, profile_ids: list[str]) -> None:
        """테넌트에 프로필 목록을 전체 교체한다."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM tenant_profiles WHERE tenant_id = $1",
                    tenant_id,
                )
                for pid in profile_ids:
                    await conn.execute(
                        "INSERT INTO tenant_profiles (tenant_id, profile_id) VALUES ($1, $2)",
                        tenant_id, pid,
                    )
        logger.info("tenant_profiles_set", tenant_id=tenant_id, count=len(profile_ids))

    async def add_profile(self, tenant_id: str, profile_id: str) -> None:
        """테넌트에 프로필을 추가한다."""
        await self._pool.execute(
            """
            INSERT INTO tenant_profiles (tenant_id, profile_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            tenant_id, profile_id,
        )

    async def remove_profile(self, tenant_id: str, profile_id: str) -> bool:
        """테넌트에서 프로필을 제거한다."""
        result = await self._pool.execute(
            "DELETE FROM tenant_profiles WHERE tenant_id = $1 AND profile_id = $2",
            tenant_id, profile_id,
        )
        return int(result.split()[-1]) > 0
