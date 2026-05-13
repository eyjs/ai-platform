"""FlowSNS Tools 단위 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.domain.agent_context import AgentContext
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError
from src.tools.internal.flowsns.tasks_tool import FlowSNSTasksTool
from src.tools.internal.flowsns.clients_tool import FlowSNSClientsTool
from src.tools.internal.flowsns.accounts_tool import FlowSNSAccountsTool
from src.tools.internal.flowsns.dashboard_tool import FlowSNSDashboardTool
from src.tools.internal.flowsns.calendar_tool import FlowSNSCalendarTool


@pytest.fixture
def mock_client():
    client = AsyncMock(spec=FlowSNSClient)
    return client


@pytest.fixture
def context_with_company():
    return AgentContext(
        session_id="test-session",
        user_id="test-user",
        metadata={"company_id": "comp-123", "source": "flowsns"},
    )


@pytest.fixture
def context_without_company():
    return AgentContext(
        session_id="test-session",
        user_id="test-user",
        metadata={},
    )


class TestFlowSNSTasksTool:
    """FlowSNSTasksTool 테스트."""

    def test_tool_metadata(self, mock_client: AsyncMock):
        tool = FlowSNSTasksTool(client=mock_client)
        assert tool.name == "flowsns_tasks"
        assert "태스크" in tool.description

    @pytest.mark.asyncio
    async def test_list_tasks(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = [
            {"id": "t1", "title": "Post A", "currentStatus": "issued"},
            {"id": "t2", "title": "Post B", "currentStatus": "in_progress"},
        ]
        tool = FlowSNSTasksTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2
        mock_client.get.assert_called_once_with("/tasks", params={})

    @pytest.mark.asyncio
    async def test_list_tasks_with_filters(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = []
        tool = FlowSNSTasksTool(client=mock_client)
        result = await tool.execute(
            {"status": "delayed", "period": "today"},
            context_with_company,
        )

        assert result.success is True
        mock_client.get.assert_called_once_with("/tasks", params={"status": "delayed", "period": "today"})

    @pytest.mark.asyncio
    async def test_get_task_detail(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"id": "t1", "title": "Post A", "description": "Detail"}
        tool = FlowSNSTasksTool(client=mock_client)
        result = await tool.execute({"taskId": "t1"}, context_with_company)

        assert result.success is True
        assert result.data["id"] == "t1"
        mock_client.get.assert_called_once_with("/tasks/t1")

    @pytest.mark.asyncio
    async def test_missing_company_id(self, mock_client: AsyncMock, context_without_company: AgentContext):
        tool = FlowSNSTasksTool(client=mock_client)
        result = await tool.execute({}, context_without_company)

        assert result.success is False
        assert "company_id" in (result.error or "")

    @pytest.mark.asyncio
    async def test_api_error(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.side_effect = FlowSNSClientError(500, "Internal Server Error")
        tool = FlowSNSTasksTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is False
        assert "Internal Server Error" in (result.error or "")


class TestFlowSNSClientsTool:
    """FlowSNSClientsTool 테스트."""

    def test_tool_metadata(self, mock_client: AsyncMock):
        tool = FlowSNSClientsTool(client=mock_client)
        assert tool.name == "flowsns_clients"

    @pytest.mark.asyncio
    async def test_list_clients(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = [{"id": "c1", "name": "Client A"}]
        tool = FlowSNSClientsTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is True
        assert result.metadata["count"] == 1
        mock_client.get.assert_called_once_with("/clients")

    @pytest.mark.asyncio
    async def test_get_client_detail(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"id": "c1", "name": "Client A"}
        tool = FlowSNSClientsTool(client=mock_client)
        result = await tool.execute({"clientId": "c1"}, context_with_company)

        assert result.success is True
        mock_client.get.assert_called_once_with("/clients/c1")

    @pytest.mark.asyncio
    async def test_get_client_stats(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"totalTasks": 10, "completedTasks": 5}
        tool = FlowSNSClientsTool(client=mock_client)
        result = await tool.execute({"clientId": "c1", "includeStats": True}, context_with_company)

        assert result.success is True
        assert result.metadata["action"] == "stats"
        mock_client.get.assert_called_once_with("/clients/c1/stats")


class TestFlowSNSAccountsTool:
    """FlowSNSAccountsTool 테스트."""

    def test_tool_metadata(self, mock_client: AsyncMock):
        tool = FlowSNSAccountsTool(client=mock_client)
        assert tool.name == "flowsns_accounts"

    @pytest.mark.asyncio
    async def test_list_accounts(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = [{"id": "a1", "platform": "instagram"}]
        tool = FlowSNSAccountsTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is True
        assert result.metadata["count"] == 1

    @pytest.mark.asyncio
    async def test_get_account_detail(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"id": "a1", "platform": "instagram"}
        tool = FlowSNSAccountsTool(client=mock_client)
        result = await tool.execute({"accountId": "a1"}, context_with_company)

        assert result.success is True
        mock_client.get.assert_called_once_with("/accounts/a1")


class TestFlowSNSDashboardTool:
    """FlowSNSDashboardTool 테스트."""

    def test_tool_metadata(self, mock_client: AsyncMock):
        tool = FlowSNSDashboardTool(client=mock_client)
        assert tool.name == "flowsns_dashboard"

    @pytest.mark.asyncio
    async def test_get_stats(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"total": 50, "completed": 30, "delayed": 5}
        tool = FlowSNSDashboardTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is True
        assert result.data["total"] == 50
        mock_client.get.assert_called_once_with("/dashboard/stats")


class TestFlowSNSCalendarTool:
    """FlowSNSCalendarTool 테스트."""

    def test_tool_metadata(self, mock_client: AsyncMock):
        tool = FlowSNSCalendarTool(client=mock_client)
        assert tool.name == "flowsns_calendar"

    @pytest.mark.asyncio
    async def test_calendar_grouping(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = [
            {"id": "t1", "title": "Post A", "dueDate": "2026-05-15T09:00:00Z", "currentStatus": "issued", "priority": "normal", "platforms": ["instagram"]},
            {"id": "t2", "title": "Post B", "dueDate": "2026-05-15T14:00:00Z", "currentStatus": "in_progress", "priority": "urgent", "platforms": ["naver_blog"]},
            {"id": "t3", "title": "Post C", "dueDate": "2026-05-16T10:00:00Z", "currentStatus": "issued", "priority": "normal", "platforms": []},
        ]
        tool = FlowSNSCalendarTool(client=mock_client)
        result = await tool.execute(
            {"dateFrom": "2026-05-15", "dateTo": "2026-05-16"},
            context_with_company,
        )

        assert result.success is True
        calendar = result.data["calendar"]
        assert "2026-05-15" in calendar
        assert "2026-05-16" in calendar
        assert len(calendar["2026-05-15"]) == 2
        assert len(calendar["2026-05-16"]) == 1
        assert result.data["totalTasks"] == 3
        assert result.metadata["days"] == 2

    @pytest.mark.asyncio
    async def test_calendar_empty(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = []
        tool = FlowSNSCalendarTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is True
        assert result.data["totalTasks"] == 0
        assert result.data["calendar"] == {}
