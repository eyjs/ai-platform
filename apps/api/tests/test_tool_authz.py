"""도구 실행단 하드 인가 테스트 (F19, Step 15).

프롬프트 레벨 제한(Profile.tools)과 별개로, 실행 직전 역할 기반 하드체크가
registry 경로와 에이전틱(tool_adapter) 경로 양쪽에서 동작하는지 검증한다.
"""

from __future__ import annotations

import pytest

from src.domain.agent_context import AgentContext
from src.domain.models import SearchScope, UserRole
from src.tools.authz import authorize_tool
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry


# NOTE: registry의 Protocol isinstance는 시그니처가 아닌 속성 존재만 검사해
# ScopedTool로 우선 매칭된다 — 테스트 도구는 scope 시그니처로 정의하고
# registry.execute에 scope를 전달한다 (tool_adapter는 시그니처 기반 판별).


class _ReadOnlyTool:
    """메타 미선언 도구 — 제한 없음 (기존 동작 보존)."""

    name = "read_only"
    description = "읽기 전용"
    input_schema: dict = {"type": "object", "properties": {}}

    async def execute(self, params: dict, context: AgentContext, scope=None) -> ToolResult:
        return ToolResult.ok("read")


class _MutatingTool:
    """EDITOR 이상만 실행 가능한 변이 도구."""

    name = "mutating"
    description = "상태 변경"
    input_schema: dict = {"type": "object", "properties": {}}
    required_role = UserRole.EDITOR

    async def execute(self, params: dict, context: AgentContext, scope=None) -> ToolResult:
        return ToolResult.ok("mutated")


class _MisconfiguredTool:
    """알 수 없는 요구 역할 — fail-closed."""

    name = "misconfigured"
    description = "메타 오류"
    input_schema: dict = {"type": "object", "properties": {}}
    required_role = "SUPERUSER"  # ROLE_HIERARCHY에 없음

    async def execute(self, params: dict, context: AgentContext) -> ToolResult:
        return ToolResult.ok("should not run")


def _ctx(role: str) -> AgentContext:
    return AgentContext(session_id="s", user_id="u", user_role=role)


class TestAuthorizeTool:
    def test_no_metadata_allows_any_role(self):
        assert authorize_tool(_ReadOnlyTool(), _ctx(UserRole.VIEWER)) is None

    def test_lower_role_denied(self):
        denial = authorize_tool(_MutatingTool(), _ctx(UserRole.VIEWER))
        assert denial is not None
        assert "EDITOR" in denial

    def test_equal_role_allowed(self):
        assert authorize_tool(_MutatingTool(), _ctx(UserRole.EDITOR)) is None

    def test_higher_role_allowed(self):
        assert authorize_tool(_MutatingTool(), _ctx(UserRole.ADMIN)) is None

    def test_unknown_user_role_denied(self):
        """위조/누락된 역할 문자열은 보수적으로 거부."""
        assert authorize_tool(_MutatingTool(), _ctx("HACKER")) is not None
        assert authorize_tool(_MutatingTool(), _ctx("")) is not None

    def test_misconfigured_required_role_fail_closed(self):
        denial = authorize_tool(_MisconfiguredTool(), _ctx(UserRole.ADMIN))
        assert denial is not None
        assert "올바르지 않아" in denial


_SCOPE = SearchScope(domain_codes=[], security_level_max="PUBLIC")


class TestRegistryHardCheck:
    async def test_registry_execute_denies_lower_role(self):
        registry = ToolRegistry()
        registry.register(_MutatingTool())

        result = await registry.execute("mutating", {}, _ctx(UserRole.VIEWER), scope=_SCOPE)

        assert result.success is False
        assert "EDITOR" in (result.error or "")

    async def test_registry_execute_allows_sufficient_role(self):
        registry = ToolRegistry()
        registry.register(_MutatingTool())

        result = await registry.execute("mutating", {}, _ctx(UserRole.EDITOR), scope=_SCOPE)

        assert result.success is True
        assert result.data == "mutated"

    async def test_registry_execute_unrestricted_tool_unchanged(self):
        registry = ToolRegistry()
        registry.register(_ReadOnlyTool())

        result = await registry.execute("read_only", {}, _ctx(UserRole.VIEWER), scope=_SCOPE)

        assert result.success is True


class TestAgenticPathHardCheck:
    """tool_adapter 경로 — 레지스트리를 우회하는 LangChain 직접 실행도 차단."""

    async def test_agentic_invoke_denies_lower_role(self):
        from src.agent.tool_adapter import convert_tools_to_langchain

        scope = SearchScope(domain_codes=[], security_level_max="PUBLIC")
        lc_tools = convert_tools_to_langchain(
            [_MutatingTool()], _ctx(UserRole.VIEWER), scope,
        )

        output = await lc_tools[0].coroutine()

        assert "EDITOR" in output
        assert "mutated" not in output

    async def test_agentic_invoke_allows_sufficient_role(self):
        from src.agent.tool_adapter import convert_tools_to_langchain

        scope = SearchScope(domain_codes=[], security_level_max="PUBLIC")
        lc_tools = convert_tools_to_langchain(
            [_MutatingTool()], _ctx(UserRole.EDITOR), scope,
        )

        output = await lc_tools[0].coroutine()

        assert "mutated" in output


class TestFlowSNSTaskActionsDeclaration:
    def test_task_actions_requires_editor(self):
        """변이 도구(flowsns_task_actions)는 EDITOR 이상을 선언해야 한다."""
        from src.tools.internal.flowsns.task_actions_tool import FlowSNSTaskActionsTool

        assert FlowSNSTaskActionsTool.required_role == UserRole.EDITOR
