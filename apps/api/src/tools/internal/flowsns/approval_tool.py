"""FlowSNS Approval Tool: 고객사 승인 요청/이력 조회."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError

logger = logging.getLogger(__name__)


class FlowSNSApprovalTool:
    """FlowSNS 고객사 승인 요청 및 이력 조회 도구.

    company_id는 AgentContext.metadata에서 자동 주입된다.
    """

    name = "flowsns_approval"
    description = (
        "FlowSNS 고객사 승인 요청/이력 조회 — 작업의 외부 승인 프로세스 관리"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["request", "logs"],
                "description": "수행할 액션 (request | logs)",
            },
            "taskId": {
                "type": "string",
                "description": "태스크 UUID (필수)",
            },
            "expiresInHours": {
                "type": "integer",
                "description": "승인 링크 만료 시간 (시간 단위, 기본값: 72, request 액션 전용)",
            },
        },
        "required": ["action", "taskId"],
    }

    def __init__(self, client: FlowSNSClient):
        self._client = client

    async def execute(self, params: dict, context: AgentContext) -> ToolResult:
        company_id = context.metadata.get("company_id")
        if not company_id:
            return ToolResult.fail("company_id not found in context metadata")

        action = params.get("action")
        task_id = params.get("taskId")

        try:
            if action == "request":
                expires_in_hours = params.get("expiresInHours", 72)
                body: dict[str, Any] = {"expiresInHours": expires_in_hours}
                data = await self._client.post(
                    f"/tasks/{task_id}/request-client-approval",
                    json=body,
                )
                return ToolResult.ok(data, tool="flowsns_approval", action="request")

            elif action == "logs":
                data = await self._client.get(f"/tasks/{task_id}/approval-logs")
                count = len(data) if isinstance(data, list) else 0
                return ToolResult.ok(data, tool="flowsns_approval", action="logs", count=count)

            else:
                return ToolResult.fail(f"Unknown action: {action!r}. Must be request or logs.")

        except FlowSNSClientError as e:
            return ToolResult.fail(
                f"FlowSNS API error: {e.detail}",
                status_code=e.status_code,
            )
