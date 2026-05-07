"""Workflow Store: YAML seed + PostgreSQL CRUD + 메모리 캐시.

ProfileStore와 동일한 패턴:
    시드 로딩 → DB 저장 → 메모리 캐시 → 동적 조회.

Admin API로 DB를 수정하면 캐시 무효화 후 다음 요청부터 새 정의가 적용된다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import asyncpg
import yaml

from src.observability.logging import get_logger
from src.workflow.definition import WorkflowDefinition, WorkflowStep

logger = get_logger(__name__)


class WorkflowStore:
    """YAML seed + PostgreSQL 기반 워크플로우 정의 저장소."""

    def __init__(self, pool: Optional[asyncpg.Pool] = None, seed_dir: str = "seeds/workflows") -> None:
        self._pool = pool
        self._seed_dir = Path(seed_dir)
        self._cache: dict[str, WorkflowDefinition] = {}

    @property
    def count(self) -> int:
        return len(self._cache)

    async def load_seeds(self) -> int:
        """YAML 시드 파일을 DB에 로딩한다."""
        if not self._seed_dir.exists():
            logger.warning("workflow_seed_dir_missing", path=str(self._seed_dir))
            return 0

        count = 0
        for yaml_file in sorted(self._seed_dir.glob("*.yaml")):
            try:
                definition = _parse_yaml(yaml_file)
                if self._pool:
                    await self._upsert(definition)
                self._cache[definition.id] = definition
                count += 1
                logger.info(
                    "workflow_seed_loaded",
                    workflow_id=definition.id,
                    name=definition.name,
                    steps=len(definition.steps),
                )
            except Exception as e:
                logger.error("workflow_seed_error", file=str(yaml_file), error=str(e))

        return count

    async def load_from_directory(self, directory: str | Path) -> None:
        """하위 호환: 디렉토리에서 YAML을 로드한다 (DB 없이)."""
        self._seed_dir = Path(directory)
        await self.load_seeds()

    def get(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """워크플로우 조회 (캐시 우선)."""
        return self._cache.get(workflow_id)

    async def get_async(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """워크플로우 조회 (캐시 → DB fallback)."""
        if workflow_id in self._cache:
            return self._cache[workflow_id]

        if not self._pool:
            return None

        row = await self._pool.fetchrow(
            "SELECT id, name, description, steps, escape_policy, max_retries, first_step, escape_keywords "
            "FROM workflows WHERE id = $1 AND is_active = TRUE",
            workflow_id,
        )
        if not row:
            return None

        definition = self._row_to_definition(row)
        self._cache[workflow_id] = definition
        return definition

    def list_all(self) -> list[WorkflowDefinition]:
        return list(self._cache.values())

    async def list_all_async(self) -> list[WorkflowDefinition]:
        """DB에서 모든 활성 워크플로우를 조회한다."""
        if not self._pool:
            return self.list_all()

        rows = await self._pool.fetch(
            "SELECT id, name, description, steps, escape_policy, max_retries, first_step, escape_keywords "
            "FROM workflows WHERE is_active = TRUE ORDER BY name"
        )
        return [self._row_to_definition(row) for row in rows]

    async def create(self, definition: WorkflowDefinition) -> None:
        """워크플로우를 DB에 생성하고 캐시에 반영한다."""
        if self._pool:
            await self._upsert(definition)
        self._cache[definition.id] = definition

    async def update(self, definition: WorkflowDefinition) -> bool:
        """워크플로우를 업데이트한다."""
        if not self._pool:
            self._cache[definition.id] = definition
            return True

        result = await self._pool.execute(
            """
            UPDATE workflows
            SET name = $2, description = $3, steps = $4::jsonb,
                escape_policy = $5, max_retries = $6, first_step = $7,
                escape_keywords = $8::jsonb,
                updated_at = NOW()
            WHERE id = $1
            """,
            definition.id,
            definition.name,
            definition.description,
            json.dumps(_steps_to_list(definition.steps), ensure_ascii=False),
            definition.escape_policy,
            definition.max_retries,
            definition.first_step,
            json.dumps(definition.escape_keywords, ensure_ascii=False),
        )
        updated = int(result.split()[-1]) > 0
        if updated:
            self._cache[definition.id] = definition
        return updated

    async def delete(self, workflow_id: str) -> bool:
        """워크플로우를 비활성화한다 (soft delete)."""
        if not self._pool:
            return self._cache.pop(workflow_id, None) is not None

        result = await self._pool.execute(
            "UPDATE workflows SET is_active = FALSE, updated_at = NOW() WHERE id = $1",
            workflow_id,
        )
        self._cache.pop(workflow_id, None)
        return int(result.split()[-1]) > 0

    def invalidate_cache(self, workflow_id: Optional[str] = None) -> None:
        """캐시를 무효화한다. workflow_id가 None이면 전체 캐시 클리어."""
        if workflow_id:
            self._cache.pop(workflow_id, None)
        else:
            self._cache.clear()

    async def _upsert(self, definition: WorkflowDefinition) -> None:
        steps_json = json.dumps(_steps_to_list(definition.steps), ensure_ascii=False)
        escape_kw_json = json.dumps(definition.escape_keywords, ensure_ascii=False)
        await self._pool.execute(
            """
            INSERT INTO workflows (id, name, description, steps, escape_policy, max_retries, first_step, escape_keywords)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8::jsonb)
            ON CONFLICT (id) DO UPDATE
                SET name = $2, description = $3, steps = $4::jsonb,
                    escape_policy = $5, max_retries = $6, first_step = $7,
                    escape_keywords = $8::jsonb,
                    updated_at = NOW()
            """,
            definition.id,
            definition.name,
            definition.description,
            steps_json,
            definition.escape_policy,
            definition.max_retries,
            definition.first_step,
            escape_kw_json,
        )

    @staticmethod
    def _row_to_definition(row: asyncpg.Record) -> WorkflowDefinition:
        steps_data = row["steps"] if isinstance(row["steps"], list) else json.loads(row["steps"])
        steps = [_parse_step(s) for s in steps_data]
        escape_keywords_raw = row.get("escape_keywords", [])
        if isinstance(escape_keywords_raw, str):
            escape_keywords_raw = json.loads(escape_keywords_raw)
        return WorkflowDefinition(
            id=row["id"],
            name=row["name"],
            description=row.get("description", ""),
            first_step=row.get("first_step", ""),
            steps=steps,
            escape_policy=row.get("escape_policy", "allow"),
            max_retries=row.get("max_retries", 3),
            escape_keywords=escape_keywords_raw or [],
        )


def _parse_step(data: dict) -> WorkflowStep:
    return WorkflowStep(
        id=data["id"],
        type=data.get("type", "message"),
        prompt=data.get("prompt", ""),
        save_as=data.get("save_as", ""),
        options=data.get("options", []),
        branches=data.get("branches", {}),
        next=data.get("next"),
        tool=data.get("tool"),
        tool_params=data.get("tool_params", {}),
        validation=data.get("validation", ""),
        # action step 전용 필드
        endpoint=data.get("endpoint", ""),
        http_method=data.get("http_method", "POST"),
        headers_template=data.get("headers_template", {}),
        payload_template=data.get("payload_template", {}),
        timeout_seconds=data.get("timeout_seconds", 30),
        on_success_message=data.get("on_success_message", ""),
        on_error_message=data.get("on_error_message", ""),
    )


def _steps_to_list(steps: list[WorkflowStep]) -> list[dict]:
    result = []
    for s in steps:
        d = {"id": s.id, "type": s.type}
        if s.prompt:
            d["prompt"] = s.prompt
        if s.save_as:
            d["save_as"] = s.save_as
        if s.options:
            d["options"] = s.options
        if s.branches:
            d["branches"] = s.branches
        if s.next:
            d["next"] = s.next
        if s.tool:
            d["tool"] = s.tool
        if s.tool_params:
            d["tool_params"] = s.tool_params
        if s.validation:
            d["validation"] = s.validation
        # action step 전용 필드
        if s.endpoint:
            d["endpoint"] = s.endpoint
        if s.http_method != "POST":
            d["http_method"] = s.http_method
        if s.headers_template:
            d["headers_template"] = s.headers_template
        if s.payload_template:
            d["payload_template"] = s.payload_template
        if s.timeout_seconds != 30:
            d["timeout_seconds"] = s.timeout_seconds
        if s.on_success_message:
            d["on_success_message"] = s.on_success_message
        if s.on_error_message:
            d["on_error_message"] = s.on_error_message
        result.append(d)
    return result


def _parse_yaml(path: Path) -> WorkflowDefinition:
    """YAML 파일을 WorkflowDefinition으로 파싱한다."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    steps = [_parse_step(s) for s in raw.get("steps", [])]

    return WorkflowDefinition(
        id=raw["id"],
        name=raw["name"],
        description=raw.get("description", ""),
        first_step=raw.get("first_step", ""),
        steps=steps,
        escape_policy=raw.get("escape_policy", "allow"),
        max_retries=raw.get("max_retries", 3),
        escape_keywords=raw.get("escape_keywords", []),
    )
