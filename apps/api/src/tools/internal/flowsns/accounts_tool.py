"""FlowSNS Accounts Tool: SNS 계정 조회."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError

logger = logging.getLogger(__name__)


class FlowSNSAccountsTool:
    """FlowSNS SNS 계정 조회 도구."""

    name = "flowsns_accounts"
    description = (
        "FlowSNS SNS 계정(인스타그램, 네이버블로그, 페이스북 등) 목록을 조회합니다. "
        "전체 목록 또는 특정 계정 상세를 조회할 수 있습니다."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "accountId": {
                "type": "string",
                "description": "특정 계정 UUID (상세 조회 시)",
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
            account_id = params.get("accountId")
            if account_id:
                data = await self._client.get(f"/accounts/{account_id}")
                return ToolResult.ok(data, tool="flowsns_accounts", action="detail")

            data = await self._client.get("/accounts")
            count = len(data) if isinstance(data, list) else 0
            return ToolResult.ok(data, tool="flowsns_accounts", action="list", count=count)

        except FlowSNSClientError as e:
            return ToolResult.fail(
                f"FlowSNS API error: {e.detail}",
                status_code=e.status_code,
            )
