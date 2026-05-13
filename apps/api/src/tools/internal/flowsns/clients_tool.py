"""FlowSNS Clients Tool: 고객사 조회."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError

logger = logging.getLogger(__name__)


class FlowSNSClientsTool:
    """FlowSNS 고객사 조회 도구."""

    name = "flowsns_clients"
    description = (
        "FlowSNS 고객사(클라이언트) 목록을 조회합니다. "
        "전체 목록 또는 특정 고객사 상세/통계를 조회할 수 있습니다."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "clientId": {
                "type": "string",
                "description": "특정 고객사 UUID (상세 조회 시)",
            },
            "includeStats": {
                "type": "boolean",
                "description": "통계 포함 여부 (clientId 필수)",
                "default": False,
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
            client_id = params.get("clientId")
            include_stats = params.get("includeStats", False)

            if client_id and include_stats:
                data = await self._client.get(f"/clients/{client_id}/stats")
                return ToolResult.ok(data, tool="flowsns_clients", action="stats")

            if client_id:
                data = await self._client.get(f"/clients/{client_id}")
                return ToolResult.ok(data, tool="flowsns_clients", action="detail")

            data = await self._client.get("/clients")
            count = len(data) if isinstance(data, list) else 0
            return ToolResult.ok(data, tool="flowsns_clients", action="list", count=count)

        except FlowSNSClientError as e:
            return ToolResult.fail(
                f"FlowSNS API error: {e.detail}",
                status_code=e.status_code,
            )
