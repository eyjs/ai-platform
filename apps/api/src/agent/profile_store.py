"""ProfileStore: YAML seed 로딩 + PostgreSQL CRUD.

프로필 관리: 시드 로딩 → DB 저장 → 메모리 캐시.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

import asyncpg
import yaml

from src.domain.models import AgentMode
from src.domain.agent_profile import AgentProfile, HybridTrigger, IntentHint, ToolRef
from src.observability.logging import get_logger
from src.router.token_match import MIN_PATTERN_LENGTH, is_valid_pattern

logger = get_logger(__name__)


class ProfileStore:
    """YAML seed + PostgreSQL 기반 프로필 저장소."""

    def __init__(self, pool: asyncpg.Pool, seed_dir: str = "seeds/profiles"):
        self._pool = pool
        self._seed_dir = Path(seed_dir)
        self._cache: dict[str, AgentProfile] = {}
        self._watching: bool = False
        self._mtimes: dict[str, float] = {}
        self._watch_task: Optional[asyncio.Task] = None

    @property
    def profile_count(self) -> int:
        """캐시된 프로필 수."""
        return len(self._cache)

    async def load_seeds(self) -> int:
        """YAML 시드 파일을 DB에 심는다 — 없는 id만. 심은 개수를 반환한다.

        이미 있는 id는 건드리지 않는다. 예전엔 부팅마다 전 시드를 upsert 해서 관리자
        UI로 고친 프로필이 재시작 한 번에 YAML 내용으로 되돌아갔다(=편집이 불가능).
        DB가 살아있는 프로필의 진실원천이고, 시드는 부트스트랩 수단일 뿐이다.

        절대규칙 1("YAML 추가만으로 새 챗봇이 동작")은 그대로다 — 없는 id는 INSERT 된다.
        운영 중인 프로필의 YAML을 고쳐 반영하려면 파일 워처(_reload_single_profile)가
        여전히 upsert 한다. 그건 파일을 실제로 건드린 명시적 행위라 덮어써도 된다.
        """
        if not self._seed_dir.exists():
            logger.warning("seed_directory_not_found", seed_dir=str(self._seed_dir))
            return 0

        seeded = 0
        for path in sorted(self._seed_dir.glob("*.yaml")):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
                profile = self._parse_profile(data)
                if await self._insert_if_absent(profile):
                    self._cache[profile.id] = profile
                    seeded += 1
                    logger.info("profile_seeded", profile_id=profile.id, name=profile.name)
                else:
                    # DB 값이 이긴다. 캐시에는 시드가 아니라 DB 버전을 올린다(get 이 DB에서
                    # 읽어 캐싱한다). 시드를 올리면 런타임이 DB 대신 YAML 로 동작해 INSERT 를
                    # 건너뛴 의미가 사라지고, 아무것도 안 올리면 profile_count(=/health 의
                    # profiles_loaded)가 0으로 보여 프로필이 없는 것처럼 읽힌다.
                    await self.get(profile.id)
                    logger.info(
                        "profile_seed_skipped_db_wins",
                        profile_id=profile.id,
                        path=path.name,
                    )
            except Exception as e:
                logger.error("seed_load_failed", path=path.name, error=str(e))

        logger.info("seed_scan_done", seeded=seeded, available=len(self._cache))
        return len(self._cache)

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

    async def _insert_if_absent(self, profile: AgentProfile) -> bool:
        """없을 때만 INSERT. 이미 있으면 아무것도 하지 않고 False 를 반환한다."""
        config_json = json.dumps(self._profile_to_dict(profile), ensure_ascii=False)
        result = await self._pool.execute(
            """
            INSERT INTO agent_profiles (id, name, description, config)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            profile.id, profile.name, profile.description, config_json,
        )
        # asyncpg 는 "INSERT 0 1" / "INSERT 0 0" 을 돌려준다.
        return int(result.split()[-1]) > 0

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
    def _parse_intent_hint(h: dict, profile_id: str, idx: int) -> IntentHint:
        """intent_hint 한 건을 방어적으로 파싱한다.

        로더 스키마는 `name`/`patterns`(복수, 리스트)를 요구하지만,
        구버전 프로필이 `intent`/`pattern`(단수, 문자열)을 쓸 수 있어 폴백을 둔다.
        둘 다 없으면 어느 프로필/어느 hint인지 포함한 명확한 에러를 던진다.
        """
        # name 폴백: name → intent
        name = h.get("name") or h.get("intent")
        if not name:
            raise ValueError(
                f"intent_hint[{idx}] in profile '{profile_id}' must have 'name' "
                f"(or legacy 'intent'). Got keys: {list(h.keys())}"
            )

        # patterns 폴백: patterns(리스트) → pattern(단수 문자열 → 1-요소 리스트)
        patterns = h.get("patterns")
        if patterns is None:
            single = h.get("pattern")
            if single is None:
                raise ValueError(
                    f"intent_hint '{name}' in profile '{profile_id}' must have "
                    f"'patterns' (list) or legacy 'pattern' (str). Got keys: {list(h.keys())}"
                )
            patterns = [single]
        if not isinstance(patterns, list):
            patterns = [patterns]

        # 1글자 패턴 거부(진단 V4): "건" 하나가 조건·안건·건강·물건에 전부 걸려
        # "이 조건이 궁금해요"를 TASK로 오태깅했다. 토큰 경계 매칭으로도 못 막는다 —
        # '조건'이 한 토큰이라 꼬리 규칙이 통하지 않는다. 그래서 로드에서 걷어낸다.
        # 조용히 버리면 작성자가 패턴이 죽은 줄 모르므로 반드시 남긴다.
        too_short = [p for p in patterns if not is_valid_pattern(str(p))]
        if too_short:
            kept = [p for p in patterns if is_valid_pattern(str(p))]
            logger.warning(
                "intent_hint_patterns_rejected",
                profile_id=profile_id,
                hint=name,
                patterns=too_short,
                remaining=len(kept),
                reason=f"{MIN_PATTERN_LENGTH}글자 미만은 오탐만 만든다 — 더 긴 표현으로 바꿀 것",
            )
            if not kept:
                # 남는 패턴이 없으면 이 인텐트는 영영 매칭되지 않는다 — 오탐을 막으려다
                # 인텐트를 통째로 죽이는 건 다른 종류의 사고다. 조용히 넘기지 않는다.
                raise ValueError(
                    f"intent_hint '{name}' in profile '{profile_id}': 모든 패턴이 "
                    f"{MIN_PATTERN_LENGTH}글자 미만({too_short})이라 이 인텐트는 매칭될 수 "
                    f"없다. 더 긴 표현으로 바꿀 것."
                )
            patterns = kept

        return IntentHint(
            name=name,
            patterns=patterns,
            description=h.get("description", ""),
        )

    @staticmethod
    def _parse_profile(data: dict) -> AgentProfile:
        if "id" not in data or "name" not in data:
            raise ValueError(f"Profile must have 'id' and 'name'. Got keys: {list(data.keys())}")
        tools = [
            ToolRef(name=t["name"], config=t.get("config", {}))
            for t in data.get("tools", [])
        ]
        profile_id = data.get("id", "<unknown>")
        intent_hints = [
            ProfileStore._parse_intent_hint(h, profile_id, idx)
            for idx, h in enumerate(data.get("intent_hints", []))
        ]
        hybrid_triggers = [
            HybridTrigger(
                keyword_patterns=t["keyword_patterns"],
                intent_types=t["intent_types"],
                workflow_id=t["workflow_id"],
                description=t.get("description", ""),
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
            rag_min_rerank_score=data.get("rag_min_rerank_score"),
            system_prompt=data.get("system_prompt", ""),
            response_policy=data.get("response_policy", "balanced"),
            guardrails=data.get("guardrails", []),
            main_model=data.get("main_model", ""),
            max_output_tokens=data.get("max_output_tokens"),
            memory_type=data.get("memory_type", "short"),
            memory_ttl_seconds=data.get("memory_ttl_seconds", 3600),
            memory_scopes=data.get("memory_scopes", ["local"]),
            memory_project_id=data.get("memory_project_id"),
            memory_max_turns=data.get("memory_max_turns", 10),
            memory_retention_days=data.get("memory_retention_days"),
            max_tool_calls=data.get("max_tool_calls", 5),
            agent_timeout_seconds=data.get("agent_timeout_seconds", 30),
            intent_hints=intent_hints,
            workflow_action_endpoint=data.get("workflow_action_endpoint"),
            workflow_action_headers=data.get("workflow_action_headers", {}),
            context_adapter=data.get("context_adapter"),
            cache_padding_text=data.get("cache_padding_text", ""),
            empty_response_fallback=data.get("empty_response_fallback"),
            planning_disabled=data.get("planning_disabled", False),
            cache_config=data.get("cache", data.get("cache_config", {})) or {},
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
                {"keyword_patterns": t.keyword_patterns, "intent_types": t.intent_types, "workflow_id": t.workflow_id, "description": t.description}
                for t in profile.hybrid_triggers
            ],
            "tools": [{"name": t.name, "config": t.config} for t in profile.tools],
            "rag_min_rerank_score": profile.rag_min_rerank_score,
            "system_prompt": profile.system_prompt,
            "response_policy": profile.response_policy,
            "guardrails": profile.guardrails,
            "main_model": profile.main_model,
            "max_output_tokens": profile.max_output_tokens,
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
            "workflow_action_endpoint": profile.workflow_action_endpoint,
            "workflow_action_headers": profile.workflow_action_headers,
            "context_adapter": profile.context_adapter,
            "cache_padding_text": profile.cache_padding_text,
            "empty_response_fallback": profile.empty_response_fallback,
            "planning_disabled": profile.planning_disabled,
            "cache_config": profile.cache_config,
        }

    def start_watcher(self) -> None:
        """YAML 파일 변경 감지를 위한 watcher 시작."""
        if self._watching:
            return
        self._watching = True
        self._watch_task = asyncio.create_task(self._watch_profiles())
        logger.info("profile_watcher_started", seed_dir=str(self._seed_dir))

    def stop_watcher(self) -> None:
        """Watcher 정지."""
        self._watching = False
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
        logger.info("profile_watcher_stopped")

    async def _watch_profiles(self) -> None:
        """30초 간격으로 YAML 파일 변경 감지 및 reload."""
        while self._watching:
            try:
                await asyncio.sleep(30)
                if not self._watching:
                    break
                await self._check_and_reload()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("profile_watch_error", error=str(e), exc_info=True)

    async def _check_and_reload(self) -> None:
        """YAML 파일들의 mtime을 확인하고 변경된 파일을 reload."""
        if not self._seed_dir.exists():
            return

        for path in self._seed_dir.glob("*.yaml"):
            try:
                current_mtime = path.stat().st_mtime
                previous_mtime = self._mtimes.get(str(path))

                if previous_mtime is None:
                    # 처음 발견한 파일: mtime만 기록
                    self._mtimes[str(path)] = current_mtime
                elif current_mtime > previous_mtime:
                    # 파일이 변경됨: reload 수행
                    logger.info("profile_file_changed", path=path.name, mtime=current_mtime)
                    await self._reload_single_profile(path)
                    self._mtimes[str(path)] = current_mtime
            except Exception as e:
                logger.error("profile_reload_failed", path=path.name, error=str(e))

    async def _reload_single_profile(self, path: Path) -> None:
        """단일 프로필 파일을 reload."""
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            profile = self._parse_profile(data)
            await self._upsert(profile)
            self._cache[profile.id] = profile
            logger.info("profile_reloaded", profile_id=profile.id, path=path.name)
        except Exception as e:
            logger.error("profile_reload_failed", path=path.name, error=str(e))
