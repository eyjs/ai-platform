"""FlowSNS Tasks Tool: 태스크 조회/검색."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError

logger = logging.getLogger(__name__)


class FlowSNSTasksTool:
    """FlowSNS 태스크 조회 도구.

    company_id는 AgentContext.metadata에서 자동 주입된다.
    """

    name = "flowsns_tasks"
    description = (
        "FlowSNS 태스크(작업) 목록을 조회합니다. "
        "상태(status), 담당자(assigneeId), 고객사(clientId), "
        "기간(dateFrom/dateTo), 기간 프리셋(period: today/this_week/delayed), "
        "검색어(search)로 필터링할 수 있습니다. "
        "특정 태스크 상세 조회는 taskId를 지정합니다."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "taskId": {
                "type": "string",
                "description": "특정 태스크 UUID (상세 조회 시)",
            },
            "status": {
                "type": "string",
                "description": "태스크 상태 필터 (issued, in_progress, submitted, revision_requested, approved, completed)",
            },
            "assigneeId": {
                "type": "string",
                "description": "담당자 UUID",
            },
            "clientId": {
                "type": "string",
                "description": "고객사 UUID",
            },
            "dateFrom": {
                "type": "string",
                "description": "시작일 (YYYY-MM-DD)",
            },
            "dateTo": {
                "type": "string",
                "description": "종료일 (YYYY-MM-DD)",
            },
            "period": {
                "type": "string",
                "enum": ["today", "this_week", "delayed"],
                "description": "기간 프리셋",
            },
            "search": {
                "type": "string",
                "description": "제목/설명 검색어",
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
            task_id = params.get("taskId")
            if task_id:
                data = await self._client.get(f"/tasks/{task_id}")
                return ToolResult.ok(data, tool="flowsns_tasks", action="detail")

            query_params: dict[str, str] = {}
            for key in ("status", "assigneeId", "clientId", "dateFrom", "dateTo", "period", "search"):
                value = params.get(key)
                if value:
                    query_params[key] = value

            data = await self._client.get("/tasks", params=query_params)
            count = len(data) if isinstance(data, list) else 0
            return ToolResult.ok(data, tool="flowsns_tasks", action="list", count=count)

        except FlowSNSClientError as e:
            return ToolResult.fail(
                f"FlowSNS API error: {e.detail}",
                status_code=e.status_code,
            )
