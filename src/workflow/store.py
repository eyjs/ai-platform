"""Workflow Store: YAML에서 WorkflowDefinition을 로드한다."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from src.observability.logging import get_logger
from src.workflow.definition import WorkflowDefinition, WorkflowStep

logger = get_logger(__name__)


class WorkflowStore:
    """YAML 기반 워크플로우 정의 저장소."""

    def __init__(self) -> None:
        self._definitions: dict[str, WorkflowDefinition] = {}

    async def load_from_directory(self, directory: str | Path) -> None:
        """디렉토리의 모든 YAML 파일을 로드한다."""
        path = Path(directory)
        if not path.exists():
            logger.warning("workflow_dir_missing", path=str(path))
            return

        for yaml_file in sorted(path.glob("*.yaml")):
            try:
                definition = _parse_yaml(yaml_file)
                self._definitions[definition.id] = definition
                logger.info(
                    "workflow_loaded",
                    workflow_id=definition.id,
                    name=definition.name,
                    steps=len(definition.steps),
                )
            except Exception as e:
                logger.error("workflow_load_error", file=str(yaml_file), error=str(e))

    def get(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        return self._definitions.get(workflow_id)

    def list_all(self) -> list[WorkflowDefinition]:
        return list(self._definitions.values())

    @property
    def count(self) -> int:
        return len(self._definitions)


def _parse_yaml(path: Path) -> WorkflowDefinition:
    """YAML 파일을 WorkflowDefinition으로 파싱한다."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    steps = []
    for step_data in raw.get("steps", []):
        steps.append(WorkflowStep(
            id=step_data["id"],
            type=step_data.get("type", "message"),
            prompt=step_data.get("prompt", ""),
            save_as=step_data.get("save_as", ""),
            options=step_data.get("options", []),
            branches=step_data.get("branches", {}),
            next=step_data.get("next"),
            tool=step_data.get("tool"),
            tool_params=step_data.get("tool_params", {}),
            validation=step_data.get("validation", ""),
        ))

    return WorkflowDefinition(
        id=raw["id"],
        name=raw["name"],
        first_step=raw.get("first_step", ""),
        steps=steps,
        escape_policy=raw.get("escape_policy", "allow"),
    )
