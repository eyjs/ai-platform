"""Workflow Definition: 워크플로우 정의 모델.

DB 저장, YAML import 지원.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkflowStep:
    """워크플로우 단계 정의."""

    id: str
    type: str  # "llm" | "tool_call" | "decision"
    prompt: str = ""
    tool: str | None = None
    tool_params: dict = field(default_factory=dict)
    next: str | None = None
    branches: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowDefinition:
    """워크플로우 정의."""

    id: str
    name: str
    steps: list[WorkflowStep] = field(default_factory=list)
    escape_policy: str = "allow"  # "allow" | "block" | "queue"
