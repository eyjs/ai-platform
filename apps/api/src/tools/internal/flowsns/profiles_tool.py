"""FlowSNS Profiles Tool: 팀원 조회/생산성 확인."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError

logger = logging.getLogger(__name__)


class FlowSNSProfilesTool:
    """FlowSNS 팀원 프로필 및 생산성 통계 조회 도구.

    company_id는 AgentContext.metadata에서 자동 주입된다.
    """

    name = "flowsns_profiles"
    description = (
        "FlowSNS 팀원 조회/생산성 확인 — 프로필 목록, 상세, 생산성 통계"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "profileId": {
                "type": "string",
                "description": "팀원 UUID (생략 시 전체 목록 조회)",
            },
            "includeProductivity": {
                "type": "boolean",
                "description": "생산성 통계 포함 여부 (profileId 필요, 기본값: false)",
            },
            "range": {
                "type": "string",
                "description": "생산성 조회 범위 (예: weekly, monthly — includeProductivity 전용)",
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

        profile_id = params.get("profileId")
        include_productivity = params.get("includeProductivity", False)

        try:
            if not profile_id:
                data = await self._client.get("/profiles")
                count = len(data) if isinstance(data, list) else 0
                return ToolResult.ok(data, tool="flowsns_profiles", action="list", count=count)

            if include_productivity:
                query_params: dict[str, str] = {}
                range_value = params.get("range")
                if range_value:
                    query_params["range"] = range_value

                data = await self._client.get(
                    f"/profiles/{profile_id}/productivity",
                    params=query_params or None,
                )
                return ToolResult.ok(data, tool="flowsns_profiles", action="productivity")

            data = await self._client.get(f"/profiles/{profile_id}")
            return ToolResult.ok(data, tool="flowsns_profiles", action="detail")

        except FlowSNSClientError as e:
            return ToolResult.fail(
                f"FlowSNS API error: {e.detail}",
                status_code=e.status_code,
            )
