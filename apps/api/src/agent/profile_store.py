"""ProfileStore: YAML seed 로딩 + PostgreSQL CRUD.

프로필 관리: 시드 로딩 → DB 저장 → 메모리 캐시.
"""

import json
import logging
from pathlib import Path
from typing import Optional

import asyncpg
import yaml

from src.domain.models import AgentMode
from src.domain.agent_profile import AgentProfile, HybridTrigger, IntentHint, ToolRef

logger = logging.getLogger(__name__)


class ProfileStore:
    """YAML seed + PostgreSQL 기반 프로필 저장소."""

    def __init__(self, pool: asyncpg.Pool, seed_dir: str = "seeds/profiles"):
        self._pool = pool
        self._seed_dir = Path(seed_dir)
        self._cache: dict[str, AgentProfile] = {}

    @property
    def profile_count(self) -> int:
        """캐시된 프로필 수."""
        return len(self._cache)

    async def load_seeds(self) -> int:
        """YAML 시드 파일을 DB에 로딩한다."""
        if not self._seed_dir.exists():
            logger.warning("Seed directory not found: %s", self._seed_dir)
            return 0

        count = 0
        for path in self._seed_dir.glob("*.yaml"):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
                profile = self._parse_profile(data)
                await self._upsert(profile)
                self._cache[profile.id] = profile
                count += 1
                logger.info("Loaded profile: %s (%s)", profile.id, profile.name)
            except Exception as e:
                logger.error("Failed to load seed %s: %s", path.name, e)

        return count

    async def get(self, profile_id: str) -> Optional[AgentProfile]:
        """프로필 조회 (캐시 → DB)."""
        if profile_id in self._cache:
            return self._cache[profile_id]

        row = await self._pool.fetchrow(
            "SELECT id, name, description, config FROM agent_profiles "
            "WHERE id = $1 AND (is_active IS NULL OR is_active = TRUE)",
            profile_id,
        )
        if not row:
            return None

        config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
        config["id"] = row["id"]
        config["name"] = row["name"]
        config["description"] = row.get("description", "")
        profile = self._parse_profile(config)
        self._cache[profile_id] = profile
        return profile

    async def list_all(self) -> list[AgentProfile]:
        """모든 활성 프로필 목록."""
        rows = await self._pool.fetch(
            "SELECT id, name, description, config FROM agent_profiles "
            "WHERE is_active IS NULL OR is_active = TRUE ORDER BY name"
        )
        profiles = []
        for row in rows:
            config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
            config["id"] = row["id"]
            config["name"] = row["name"]
            config["description"] = row.get("description", "")
            profiles.append(self._parse_profile(config))
        return profiles

    def parse_profile(self, data: dict) -> AgentProfile:
        """dict에서 AgentProfile을 생성한다. Admin API용 public 팩토리."""
        return self._parse_profile(data)

    def profile_to_dict(self, profile: AgentProfile) -> dict:
        """AgentProfile을 dict로 변환한다. Admin API용 public 직렬화."""
        result = self._profile_to_dict(profile)
        result["id"] = profile.id
        result["name"] = profile.name
        result["description"] = profile.description
        return result

    async def create(self, profile: AgentProfile) -> None:
        """프로필 생성."""
        await self._upsert(profile)
        self._cache[profile.id] = profile

    async def update(self, profile: AgentProfile) -> bool:
        """프로필 업데이트."""
        config_json = json.dumps(self._profile_to_dict(profile), ensure_ascii=False)
        result = await self._pool.execute(
            """
            UPDATE agent_profiles
            SET name = $2, description = $3, config = $4::jsonb, updated_at = NOW()
            WHERE id = $1
            """,
            profile.id, profile.name, profile.description, config_json,
        )
        updated = int(result.split()[-1]) > 0
        if updated:
            self._cache[profile.id] = profile
        return updated

    async def delete(self, profile_id: str) -> bool:
        """프로필 비활성화 (soft delete)."""
        result = await self._pool.execute(
            "UPDATE agent_profiles SET is_active = FALSE, updated_at = NOW() WHERE id = $1",
            profile_id,
        )
        self._cache.pop(profile_id, None)
        return int(result.split()[-1]) > 0

    def invalidate_cache(self, profile_id: Optional[str] = None) -> None:
        """캐시 무효화. profile_id가 None이면 전체 클리어."""
        if profile_id:
            self._cache.pop(profile_id, None)
        else:
            self._cache.clear()

    async def _upsert(self, profile: AgentProfile) -> None:
        config = self._profile_to_dict(profile)
        config_json = json.dumps(config, ensure_ascii=False)
        await self._pool.execute(
            """
            INSERT INTO agent_profiles (id, name, description, config)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (id) DO UPDATE
                SET name = $2, description = $3, config = $4::jsonb, updated_at = NOW()
            """,
            profile.id, profile.name, profile.description, config_json,
        )

    @staticmethod
    def _parse_profile(data: dict) -> AgentProfile:
        if "id" not in data or "name" not in data:
            raise ValueError(f"Profile must have 'id' and 'name'. Got keys: {list(data.keys())}")
        tools = [
            ToolRef(name=t["name"], config=t.get("config", {}))
            for t in data.get("tools", [])
        ]
        intent_hints = [
            IntentHint(
                name=h["name"], patterns=h["patterns"],
                description=h.get("description", ""),
            )
            for h in data.get("intent_hints", [])
        ]
        hybrid_triggers = [
            HybridTrigger(
                keyword_patterns=t["keyword_patterns"],
                intent_types=t["intent_types"],
                workflow_id=t["workflow_id"],
            )
            for t in data.get("hybrid_triggers", [])
        ]
        return AgentProfile(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            domain_scopes=data.get("domain_scopes", []),
            category_scopes=data.get("category_scopes", []),
            security_level_max=data.get("security_level_max", "PUBLIC"),
            include_common=data.get("include_common", True),
            mode=AgentMode(data.get("mode", "agentic")),
            workflow_id=data.get("workflow_id"),
            hybrid_triggers=hybrid_triggers,
            tools=tools,
            system_prompt=data.get("system_prompt", ""),
            response_policy=data.get("response_policy", "balanced"),
            guardrails=data.get("guardrails", []),
            router_model=data.get("router_model", "haiku"),
            main_model=data.get("main_model", "sonnet"),
            memory_type=data.get("memory_type", "short"),
            memory_ttl_seconds=data.get("memory_ttl_seconds", 3600),
            memory_scopes=data.get("memory_scopes", ["local"]),
            memory_project_id=data.get("memory_project_id"),
            memory_max_turns=data.get("memory_max_turns", 10),
            memory_retention_days=data.get("memory_retention_days"),
            max_tool_calls=data.get("max_tool_calls", 5),
            agent_timeout_seconds=data.get("agent_timeout_seconds", 30),
            intent_hints=intent_hints,
        )

    @staticmethod
    def _profile_to_dict(profile: AgentProfile) -> dict:
        return {
            "domain_scopes": profile.domain_scopes,
            "category_scopes": profile.category_scopes,
            "security_level_max": profile.security_level_max,
            "include_common": profile.include_common,
            "mode": profile.mode.value,
            "workflow_id": profile.workflow_id,
            "hybrid_triggers": [
                {"keyword_patterns": t.keyword_patterns, "intent_types": t.intent_types, "workflow_id": t.workflow_id}
                for t in profile.hybrid_triggers
            ],
            "tools": [{"name": t.name, "config": t.config} for t in profile.tools],
            "system_prompt": profile.system_prompt,
            "response_policy": profile.response_policy,
            "guardrails": profile.guardrails,
            "router_model": profile.router_model,
            "main_model": profile.main_model,
            "memory_type": profile.memory_type,
            "memory_ttl_seconds": profile.memory_ttl_seconds,
            "memory_scopes": profile.memory_scopes,
            "memory_project_id": profile.memory_project_id,
            "memory_max_turns": profile.memory_max_turns,
            "memory_retention_days": profile.memory_retention_days,
            "max_tool_calls": profile.max_tool_calls,
            "agent_timeout_seconds": profile.agent_timeout_seconds,
            "intent_hints": [
                {"name": h.name, "patterns": h.patterns, "description": h.description}
                for h in profile.intent_hints
            ],
        }
