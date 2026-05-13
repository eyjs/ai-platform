"""FlowSNS Reports Tool: 리포트 요약 조회."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError

logger = logging.getLogger(__name__)


class FlowSNSReportsTool:
    """FlowSNS 기간별/범위별 업무 통계 리포트 조회 도구.

    company_id는 AgentContext.metadata에서 자동 주입된다.
    """

    name = "flowsns_reports"
    description = (
        "FlowSNS 리포트 요약 조회 — 기간별/범위별 업무 통계 리포트"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["weekly", "monthly", "quarterly"],
                "description": "리포트 기간 (기본값: monthly)",
            },
            "year": {
                "type": "string",
                "description": "조회 연도 4자리 (예: 2025)",
            },
            "scope": {
                "type": "string",
                "enum": ["company", "team", "mine"],
                "description": "리포트 범위",
            },
            "clientId": {
                "type": "string",
                "description": "고객사 UUID 필터 (선택)",
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
            for key in ("period", "year", "scope", "clientId"):
                value = params.get(key)
                if value:
                    query_params[key] = value

            data = await self._client.get("/reports/summary", params=query_params)
            return ToolResult.ok(data, tool="flowsns_reports", action="summary")

        except FlowSNSClientError as e:
            return ToolResult.fail(
                f"FlowSNS API error: {e.detail}",
                status_code=e.status_code,
            )
