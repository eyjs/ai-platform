"""Tool System: AI 플랫폼의 Capability System.

Tool Protocol + ToolResult + ToolDefinition 정의.
Agent는 Tool 목록을 직접 알지 않는다 - ToolRegistry가 Profile.tools 기반으로 제공.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.router.execution_plan import SearchScope


@dataclass(frozen=True)
class ToolResult:
    """도구 실행 결과."""

    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def ok(cls, data: Any, **metadata: Any) -> ToolResult:
        return cls(success=True, data=data, metadata=metadata)

    @classmethod
    def fail(cls, error: str, **metadata: Any) -> ToolResult:
        return cls(success=False, error=error, metadata=metadata)


@dataclass(frozen=True)
class ToolDefinition:
    """도구 메타데이터 (LLM이 읽는 형식)."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    type: str = "internal"  # "internal" | "mcp"
    timeout_seconds: int = 5
    retry_count: int = 2
    cost_tier: str = "free"  # "free" | "low" | "high"


@dataclass
class AgentContext:
    """도구 실행 시 전달되는 대화 맥락."""

    session_id: str = ""
    user_id: str = ""
    user_role: str = "VIEWER"
    conversation_history: list = field(default_factory=list)
    prior_doc_ids: list[str] = field(default_factory=list)


@runtime_checkable
class Tool(Protocol):
    """도구 프로토콜 (SearchScope 불필요한 도구)."""

    name: str
    description: str
    input_schema: dict

    async def execute(
        self,
        params: dict,
        context: AgentContext,
    ) -> ToolResult: ...


@runtime_checkable
class ScopedTool(Protocol):
    """스코프 인식 도구 프로토콜 (검색 범위 자동 주입)."""

    name: str
    description: str
    input_schema: dict

    async def execute(
        self,
        params: dict,
        context: AgentContext,
        scope: SearchScope,
    ) -> ToolResult: ...
