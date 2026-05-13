"""FlowSNS Calendar Tool: 일정/캘린더 조회."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError

logger = logging.getLogger(__name__)


class FlowSNSCalendarTool:
    """FlowSNS 캘린더/일정 조회 도구.

    특정 기간의 태스크 마감일 기반 일정을 조회한다.
    """

    name = "flowsns_calendar"
    description = (
        "FlowSNS 캘린더에서 특정 기간의 일정을 조회합니다. "
        "마감일 기준으로 태스크를 날짜별로 그룹핑하여 반환합니다. "
        "dateFrom/dateTo로 조회 범위를 지정합니다."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "dateFrom": {
                "type": "string",
                "description": "시작일 (YYYY-MM-DD). 미지정 시 오늘.",
            },
            "dateTo": {
                "type": "string",
                "description": "종료일 (YYYY-MM-DD). 미지정 시 시작일 +7일.",
            },
            "clientId": {
                "type": "string",
                "description": "특정 고객사의 일정만 조회",
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

        try:
            query_params: dict[str, str] = {}
            for key in ("dateFrom", "dateTo", "clientId"):
                value = params.get(key)
                if value:
                    query_params[key] = value

            # 캘린더 API는 태스크 목록을 기간 필터로 조회하여 날짜별 그룹핑
            data = await self._client.get("/tasks", params=query_params)

            # 날짜별 그룹핑
            calendar: dict[str, list[dict]] = {}
            if isinstance(data, list):
                for task in data:
                    due = task.get("dueDate", "")
                    if due:
                        date_key = due[:10]  # YYYY-MM-DD
                        calendar.setdefault(date_key, []).append({
                            "id": task.get("id"),
                            "title": task.get("title"),
                            "status": task.get("currentStatus"),
                            "priority": task.get("priority"),
                            "platforms": task.get("platforms", []),
                        })

            return ToolResult.ok(
                {"calendar": calendar, "totalTasks": len(data) if isinstance(data, list) else 0},
                tool="flowsns_calendar",
                action="calendar",
                days=len(calendar),
            )

        except FlowSNSClientError as e:
            return ToolResult.fail(
                f"FlowSNS API error: {e.detail}",
                status_code=e.status_code,
            )
