"""Tool Registry: Profile.tools 기반 동적 도구 로딩.

ScopedTool에 SearchScope를 자동 주입한다.
"""

import logging
from typing import Optional, Union

from src.agent.profile import AgentProfile, ToolRef
from src.router.execution_plan import SearchScope
from src.tools.base import AgentContext, ScopedTool, Tool, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Profile.tools 기반 도구 레지스트리."""

    def __init__(self):
        self._tools: dict[str, Union[Tool, ScopedTool]] = {}

    def register(self, tool: Union[Tool, ScopedTool]) -> None:
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    def resolve(self, profile: AgentProfile) -> list[Union[Tool, ScopedTool]]:
        """Profile.tools 참조를 실제 도구 인스턴스로 해석한다."""
        resolved = []
        for ref in profile.tools:
            tool = self._tools.get(ref.name)
            if tool:
                resolved.append(tool)
            else:
                logger.warning("Tool not found: %s (profile: %s)", ref.name, profile.id)
        return resolved

    def get(self, name: str) -> Optional[Union[Tool, ScopedTool]]:
        return self._tools.get(name)

    async def execute(
        self,
        tool_name: str,
        params: dict,
        context: AgentContext,
        scope: Optional[SearchScope] = None,
    ) -> ToolResult:
        """도구 실행. ScopedTool이면 scope를 자동 주입."""
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult.fail(f"Tool not found: {tool_name}")

        try:
            if isinstance(tool, ScopedTool) and scope:
                return await tool.execute(params, context, scope)
            elif isinstance(tool, Tool):
                return await tool.execute(params, context)
            else:
                return ToolResult.fail(
                    f"ScopedTool '{tool_name}' requires SearchScope"
                )
        except Exception as e:
            logger.error("Tool '%s' execution failed: %s", tool_name, e)
            return ToolResult.fail(str(e))

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())
