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
from src.tools.internal.flowsns.task_actions_tool import FlowSNSTaskActionsTool
from src.tools.internal.flowsns.approval_tool import FlowSNSApprovalTool
from src.tools.internal.flowsns.notifications_tool import FlowSNSNotificationsTool
from src.tools.internal.flowsns.reports_tool import FlowSNSReportsTool
from src.tools.internal.flowsns.profiles_tool import FlowSNSProfilesTool


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


# ── Write Tools ──────────────────────────────────────────────


class TestFlowSNSTaskActionsTool:
    """FlowSNSTaskActionsTool 테스트."""

    def test_tool_metadata(self, mock_client: AsyncMock):
        tool = FlowSNSTaskActionsTool(client=mock_client)
        assert tool.name == "flowsns_task_actions"
        assert "생성" in tool.description

    @pytest.mark.asyncio
    async def test_create_task(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.post.return_value = {"id": "new-1", "title": "인스타 포스팅"}
        tool = FlowSNSTaskActionsTool(client=mock_client)
        result = await tool.execute(
            {
                "action": "create",
                "title": "인스타 포스팅",
                "clientId": "c1",
                "platforms": ["instagram"],
                "taskType": "post_writing",
                "dueDate": "2026-05-20",
            },
            context_with_company,
        )

        assert result.success is True
        assert result.data["id"] == "new-1"
        mock_client.post.assert_called_once_with(
            "/tasks",
            json={
                "title": "인스타 포스팅",
                "clientId": "c1",
                "platforms": ["instagram"],
                "taskType": "post_writing",
                "dueDate": "2026-05-20",
            },
        )

    @pytest.mark.asyncio
    async def test_create_task_with_optional_fields(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.post.return_value = {"id": "new-2"}
        tool = FlowSNSTaskActionsTool(client=mock_client)
        result = await tool.execute(
            {
                "action": "create",
                "title": "급한 블로그",
                "clientId": "c2",
                "platforms": ["naver_blog"],
                "taskType": "post_writing",
                "dueDate": "2026-05-14",
                "priority": "urgent",
                "assigneeId": "user-a",
                "description": "긴급 건",
            },
            context_with_company,
        )

        assert result.success is True
        call_json = mock_client.post.call_args[1]["json"]
        assert call_json["priority"] == "urgent"
        assert call_json["assigneeId"] == "user-a"
        assert call_json["description"] == "긴급 건"

    @pytest.mark.asyncio
    async def test_update_task(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.patch.return_value = {"id": "t1", "title": "수정됨"}
        tool = FlowSNSTaskActionsTool(client=mock_client)
        result = await tool.execute(
            {"action": "update", "taskId": "t1", "title": "수정됨", "priority": "urgent"},
            context_with_company,
        )

        assert result.success is True
        mock_client.patch.assert_called_once_with(
            "/tasks/t1",
            json={"title": "수정됨", "priority": "urgent"},
        )

    @pytest.mark.asyncio
    async def test_update_missing_task_id(self, mock_client: AsyncMock, context_with_company: AgentContext):
        tool = FlowSNSTaskActionsTool(client=mock_client)
        result = await tool.execute({"action": "update"}, context_with_company)

        assert result.success is False
        assert "taskId" in (result.error or "")

    @pytest.mark.asyncio
    async def test_add_revision(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.post.return_value = {"id": "rev-1", "action": "submitted"}
        tool = FlowSNSTaskActionsTool(client=mock_client)
        result = await tool.execute(
            {
                "action": "addRevision",
                "taskId": "t1",
                "revisionAction": "submitted",
                "comment": "완료했습니다",
            },
            context_with_company,
        )

        assert result.success is True
        mock_client.post.assert_called_once_with(
            "/tasks/t1/revisions",
            json={"action": "submitted", "comment": "완료했습니다"},
        )

    @pytest.mark.asyncio
    async def test_add_revision_missing_action(self, mock_client: AsyncMock, context_with_company: AgentContext):
        tool = FlowSNSTaskActionsTool(client=mock_client)
        result = await tool.execute(
            {"action": "addRevision", "taskId": "t1"},
            context_with_company,
        )

        assert result.success is False
        assert "revisionAction" in (result.error or "")

    @pytest.mark.asyncio
    async def test_unknown_action(self, mock_client: AsyncMock, context_with_company: AgentContext):
        tool = FlowSNSTaskActionsTool(client=mock_client)
        result = await tool.execute({"action": "delete"}, context_with_company)

        assert result.success is False
        assert "Unknown action" in (result.error or "")

    @pytest.mark.asyncio
    async def test_missing_company_id(self, mock_client: AsyncMock, context_without_company: AgentContext):
        tool = FlowSNSTaskActionsTool(client=mock_client)
        result = await tool.execute({"action": "create"}, context_without_company)

        assert result.success is False
        assert "company_id" in (result.error or "")

    @pytest.mark.asyncio
    async def test_api_error(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.post.side_effect = FlowSNSClientError(400, "Bad Request")
        tool = FlowSNSTaskActionsTool(client=mock_client)
        result = await tool.execute(
            {
                "action": "create",
                "title": "t",
                "clientId": "c",
                "platforms": ["instagram"],
                "taskType": "post_writing",
                "dueDate": "2026-05-20",
            },
            context_with_company,
        )

        assert result.success is False
        assert "Bad Request" in (result.error or "")


class TestFlowSNSApprovalTool:
    """FlowSNSApprovalTool 테스트."""

    def test_tool_metadata(self, mock_client: AsyncMock):
        tool = FlowSNSApprovalTool(client=mock_client)
        assert tool.name == "flowsns_approval"

    @pytest.mark.asyncio
    async def test_request_approval(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.post.return_value = {"approvalUrl": "https://...", "expiresAt": "2026-05-16"}
        tool = FlowSNSApprovalTool(client=mock_client)
        result = await tool.execute(
            {"action": "request", "taskId": "t1"},
            context_with_company,
        )

        assert result.success is True
        mock_client.post.assert_called_once_with(
            "/tasks/t1/request-client-approval",
            json={"expiresInHours": 72},
        )

    @pytest.mark.asyncio
    async def test_request_approval_custom_expiry(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.post.return_value = {"approvalUrl": "https://..."}
        tool = FlowSNSApprovalTool(client=mock_client)
        result = await tool.execute(
            {"action": "request", "taskId": "t1", "expiresInHours": 24},
            context_with_company,
        )

        assert result.success is True
        call_json = mock_client.post.call_args[1]["json"]
        assert call_json["expiresInHours"] == 24

    @pytest.mark.asyncio
    async def test_get_approval_logs(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = [
            {"id": "log1", "status": "approved"},
            {"id": "log2", "status": "pending"},
        ]
        tool = FlowSNSApprovalTool(client=mock_client)
        result = await tool.execute(
            {"action": "logs", "taskId": "t1"},
            context_with_company,
        )

        assert result.success is True
        assert result.metadata["count"] == 2
        mock_client.get.assert_called_once_with("/tasks/t1/approval-logs")

    @pytest.mark.asyncio
    async def test_unknown_action(self, mock_client: AsyncMock, context_with_company: AgentContext):
        tool = FlowSNSApprovalTool(client=mock_client)
        result = await tool.execute(
            {"action": "cancel", "taskId": "t1"},
            context_with_company,
        )

        assert result.success is False
        assert "Unknown action" in (result.error or "")

    @pytest.mark.asyncio
    async def test_missing_company_id(self, mock_client: AsyncMock, context_without_company: AgentContext):
        tool = FlowSNSApprovalTool(client=mock_client)
        result = await tool.execute({"action": "request", "taskId": "t1"}, context_without_company)

        assert result.success is False


class TestFlowSNSNotificationsTool:
    """FlowSNSNotificationsTool 테스트."""

    def test_tool_metadata(self, mock_client: AsyncMock):
        tool = FlowSNSNotificationsTool(client=mock_client)
        assert tool.name == "flowsns_notifications"

    @pytest.mark.asyncio
    async def test_list_notifications(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = [{"id": "n1", "message": "새 작업"}]
        tool = FlowSNSNotificationsTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is True
        assert result.metadata["count"] == 1
        mock_client.get.assert_called_once_with("/notifications", params={})

    @pytest.mark.asyncio
    async def test_list_with_filter(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = []
        tool = FlowSNSNotificationsTool(client=mock_client)
        result = await tool.execute({"action": "list", "filter": "unread", "level": "warning"}, context_with_company)

        assert result.success is True
        mock_client.get.assert_called_once_with("/notifications", params={"filter": "unread", "level": "warning"})

    @pytest.mark.asyncio
    async def test_unread_count(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"count": 5}
        tool = FlowSNSNotificationsTool(client=mock_client)
        result = await tool.execute({"action": "unreadCount"}, context_with_company)

        assert result.success is True
        assert result.data["count"] == 5

    @pytest.mark.asyncio
    async def test_mark_all_read(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.patch.return_value = {"updated": 3}
        tool = FlowSNSNotificationsTool(client=mock_client)
        result = await tool.execute({"action": "markAllRead"}, context_with_company)

        assert result.success is True
        mock_client.patch.assert_called_once_with("/notifications/read-all")

    @pytest.mark.asyncio
    async def test_mark_read(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.patch.return_value = {"id": "n1", "read": True}
        tool = FlowSNSNotificationsTool(client=mock_client)
        result = await tool.execute({"action": "markRead", "notificationId": "n1"}, context_with_company)

        assert result.success is True
        mock_client.patch.assert_called_once_with("/notifications/n1/read")

    @pytest.mark.asyncio
    async def test_mark_read_missing_id(self, mock_client: AsyncMock, context_with_company: AgentContext):
        tool = FlowSNSNotificationsTool(client=mock_client)
        result = await tool.execute({"action": "markRead"}, context_with_company)

        assert result.success is False
        assert "notificationId" in (result.error or "")

    @pytest.mark.asyncio
    async def test_api_error(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.side_effect = FlowSNSClientError(503, "Service Unavailable")
        tool = FlowSNSNotificationsTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is False
        assert "Service Unavailable" in (result.error or "")


class TestFlowSNSReportsTool:
    """FlowSNSReportsTool 테스트."""

    def test_tool_metadata(self, mock_client: AsyncMock):
        tool = FlowSNSReportsTool(client=mock_client)
        assert tool.name == "flowsns_reports"

    @pytest.mark.asyncio
    async def test_summary_default(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"period": "monthly", "totalTasks": 100}
        tool = FlowSNSReportsTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is True
        mock_client.get.assert_called_once_with("/reports/summary", params={})

    @pytest.mark.asyncio
    async def test_summary_with_params(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"period": "weekly"}
        tool = FlowSNSReportsTool(client=mock_client)
        result = await tool.execute(
            {"period": "weekly", "year": "2026", "scope": "team", "clientId": "c1"},
            context_with_company,
        )

        assert result.success is True
        mock_client.get.assert_called_once_with(
            "/reports/summary",
            params={"period": "weekly", "year": "2026", "scope": "team", "clientId": "c1"},
        )

    @pytest.mark.asyncio
    async def test_missing_company_id(self, mock_client: AsyncMock, context_without_company: AgentContext):
        tool = FlowSNSReportsTool(client=mock_client)
        result = await tool.execute({}, context_without_company)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_api_error(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.side_effect = FlowSNSClientError(403, "Forbidden")
        tool = FlowSNSReportsTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is False
        assert "Forbidden" in (result.error or "")


class TestFlowSNSProfilesTool:
    """FlowSNSProfilesTool 테스트."""

    def test_tool_metadata(self, mock_client: AsyncMock):
        tool = FlowSNSProfilesTool(client=mock_client)
        assert tool.name == "flowsns_profiles"

    @pytest.mark.asyncio
    async def test_list_profiles(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = [{"id": "p1", "name": "김대리"}, {"id": "p2", "name": "이과장"}]
        tool = FlowSNSProfilesTool(client=mock_client)
        result = await tool.execute({}, context_with_company)

        assert result.success is True
        assert result.metadata["count"] == 2
        mock_client.get.assert_called_once_with("/profiles")

    @pytest.mark.asyncio
    async def test_profile_detail(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"id": "p1", "name": "김대리", "role": "staff"}
        tool = FlowSNSProfilesTool(client=mock_client)
        result = await tool.execute({"profileId": "p1"}, context_with_company)

        assert result.success is True
        assert result.metadata["action"] == "detail"
        mock_client.get.assert_called_once_with("/profiles/p1")

    @pytest.mark.asyncio
    async def test_productivity(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"completedTasks": 15, "avgTime": "2h"}
        tool = FlowSNSProfilesTool(client=mock_client)
        result = await tool.execute(
            {"profileId": "p1", "includeProductivity": True, "range": "weekly"},
            context_with_company,
        )

        assert result.success is True
        assert result.metadata["action"] == "productivity"
        mock_client.get.assert_called_once_with("/profiles/p1/productivity", params={"range": "weekly"})

    @pytest.mark.asyncio
    async def test_productivity_no_range(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.return_value = {"completedTasks": 10}
        tool = FlowSNSProfilesTool(client=mock_client)
        result = await tool.execute(
            {"profileId": "p1", "includeProductivity": True},
            context_with_company,
        )

        assert result.success is True
        mock_client.get.assert_called_once_with("/profiles/p1/productivity", params=None)

    @pytest.mark.asyncio
    async def test_missing_company_id(self, mock_client: AsyncMock, context_without_company: AgentContext):
        tool = FlowSNSProfilesTool(client=mock_client)
        result = await tool.execute({}, context_without_company)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_api_error(self, mock_client: AsyncMock, context_with_company: AgentContext):
        mock_client.get.side_effect = FlowSNSClientError(404, "Not Found")
        tool = FlowSNSProfilesTool(client=mock_client)
        result = await tool.execute({"profileId": "nonexistent"}, context_with_company)

        assert result.success is False
        assert "Not Found" in (result.error or "")
