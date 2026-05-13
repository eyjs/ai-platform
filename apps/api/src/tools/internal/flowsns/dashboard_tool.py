"""FlowSNS Dashboard Tool: 대시보드 통계 조회."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError

logger = logging.getLogger(__name__)


class FlowSNSDashboardTool:
    """FlowSNS 대시보드 통계 조회 도구."""

    name = "flowsns_dashboard"
    description = (
        "FlowSNS 대시보드 통계를 조회합니다. "
        "전체 태스크 수, 상태별 분포, 오늘/이번 주 마감 건수, "
        "지연 태스크 수 등 운영 현황을 확인할 수 있습니다."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, client: FlowSNSClient):
        self._client = client

    async def execute(self, params: dict, context: AgentContext) -> ToolResult:
        company_id = context.metadata.get("company_id")
        if not company_id:
            return ToolResult.fail("company_id not found in context metadata")

        try:
            data = await self._client.get("/dashboard/stats")
            return ToolResult.ok(data, tool="flowsns_dashboard", action="stats")

        except FlowSNSClientError as e:
            return ToolResult.fail(
                f"FlowSNS API error: {e.detail}",
                status_code=e.status_code,
            )
