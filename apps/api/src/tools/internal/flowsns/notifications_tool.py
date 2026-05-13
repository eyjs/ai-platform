"""FlowSNS Notifications Tool: 알림 조회/관리."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError

logger = logging.getLogger(__name__)


class FlowSNSNotificationsTool:
    """FlowSNS 알림 조회 및 읽음 처리 도구.

    company_id는 AgentContext.metadata에서 자동 주입된다.
    """

    name = "flowsns_notifications"
    description = (
        "FlowSNS 알림 조회/관리 — 미읽은 알림 확인, 읽음 처리"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "unreadCount", "markAllRead", "markRead"],
                "description": "수행할 액션 (기본값: list)",
            },
            "notificationId": {
                "type": "string",
                "description": "알림 UUID (markRead 시 필수)",
            },
            "filter": {
                "type": "string",
                "enum": ["all", "unread", "read"],
                "description": "알림 필터 (list 전용)",
            },
            "level": {
                "type": "string",
                "enum": ["summary", "event", "warning", "info"],
                "description": "알림 레벨 필터 (list 전용)",
            },
        },
        "required": [],
    }

    def __init__(self, client: FlowSNSClient):
        self._client = client

    async def execute(self, params: dict, context: AgentContext) -> ToolResult:
        company_id = context.metadata.get("company_id")
        if not company_id:
            return ToolResult.fail("company_id not found in context metadata")

        action = params.get("action", "list")

        try:
            if action == "list":
                query_params: dict[str, str] = {}
                for key in ("filter", "level"):
                    value = params.get(key)
                    if value:
                        query_params[key] = value

                data = await self._client.get("/notifications", params=query_params)
                count = len(data) if isinstance(data, list) else 0
                return ToolResult.ok(data, tool="flowsns_notifications", action="list", count=count)

            elif action == "unreadCount":
                data = await self._client.get("/notifications/unread-count")
                return ToolResult.ok(data, tool="flowsns_notifications", action="unreadCount")

            elif action == "markAllRead":
                data = await self._client.patch("/notifications/read-all")
                return ToolResult.ok(data, tool="flowsns_notifications", action="markAllRead")

            elif action == "markRead":
                notification_id = params.get("notificationId")
                if not notification_id:
                    return ToolResult.fail("notificationId is required for markRead action")

                data = await self._client.patch(f"/notifications/{notification_id}/read")
                return ToolResult.ok(data, tool="flowsns_notifications", action="markRead")

            else:
                return ToolResult.fail(
                    f"Unknown action: {action!r}. Must be list, unreadCount, markAllRead, or markRead."
                )

        except FlowSNSClientError as e:
            return ToolResult.fail(
                f"FlowSNS API error: {e.detail}",
                status_code=e.status_code,
            )
