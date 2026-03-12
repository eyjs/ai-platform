"""ProfileStore: YAML seed 로딩 + PostgreSQL CRUD.

프로필 관리: 시드 로딩 → DB 저장 → 메모리 캐시.
"""

import json
import logging
from pathlib import Path
from typing import Optional

import asyncpg
import yaml

from .profile import AgentProfile, HybridTrigger, IntentHint, ToolRef

logger = logging.getLogger(__name__)


class ProfileStore:
    """YAML seed + PostgreSQL 기반 프로필 저장소."""

    def __init__(self, pool: asyncpg.Pool, seed_dir: str = "seeds/profiles"):
        self._pool = pool
        self._seed_dir = Path(seed_dir)
        self._cache: dict[str, AgentProfile] = {}

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
            "SELECT id, name, config FROM agent_profiles WHERE id = $1",
            profile_id,
        )
        if not row:
            return None

        config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
        config["id"] = row["id"]
        config["name"] = row["name"]
        profile = self._parse_profile(config)
        self._cache[profile_id] = profile
        return profile

    async def list_all(self) -> list[AgentProfile]:
        """모든 프로필 목록."""
        rows = await self._pool.fetch("SELECT id, name, config FROM agent_profiles")
        profiles = []
        for row in rows:
            config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
            config["id"] = row["id"]
            config["name"] = row["name"]
            profiles.append(self._parse_profile(config))
        return profiles

    async def create(self, profile: AgentProfile) -> None:
        """프로필 생성."""
        await self._upsert(profile)
        self._cache[profile.id] = profile

    async def delete(self, profile_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM agent_profiles WHERE id = $1", profile_id,
        )
        self._cache.pop(profile_id, None)
        return int(result.split()[-1]) > 0

    async def _upsert(self, profile: AgentProfile) -> None:
        config = self._profile_to_dict(profile)
        config_json = json.dumps(config, ensure_ascii=False)
        await self._pool.execute(
            """
            INSERT INTO agent_profiles (id, name, config)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (id) DO UPDATE SET name = $2, config = $3::jsonb, updated_at = NOW()
            """,
            profile.id, profile.name, config_json,
        )

    @staticmethod
    def _parse_profile(data: dict) -> AgentProfile:
        tools = [
            ToolRef(name=t["name"], config=t.get("config", {}))
            for t in data.get("tools", [])
        ]
        intent_hints = [
            IntentHint(
                name=h["name"], patterns=h["patterns"],
                description=h["description"], route_to=h["route_to"],
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
            domain_scopes=data.get("domain_scopes", []),
            category_scopes=data.get("category_scopes", []),
            security_level_max=data.get("security_level_max", "PUBLIC"),
            mode=data.get("mode", "agentic"),
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
            intent_hints=intent_hints,
        )

    @staticmethod
    def _profile_to_dict(profile: AgentProfile) -> dict:
        return {
            "domain_scopes": profile.domain_scopes,
            "category_scopes": profile.category_scopes,
            "security_level_max": profile.security_level_max,
            "mode": profile.mode,
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
            "intent_hints": [
                {"name": h.name, "patterns": h.patterns, "description": h.description, "route_to": h.route_to}
                for h in profile.intent_hints
            ],
        }
