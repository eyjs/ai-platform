"""FlowSNS Task Actions Tool: 태스크 생성/수정/리비전 추가."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I,
)


class FlowSNSTaskActionsTool:
    """FlowSNS 태스크 생성/수정/상태변경 도구.

    company_id는 AgentContext.metadata에서 자동 주입된다.
    clientId/assigneeId에 이름을 전달하면 자동으로 UUID를 조회한다.
    """

    name = "flowsns_task_actions"
    description = (
        "FlowSNS 태스크 생성/수정/상태변경 — 작업 발행, 완료 처리, 담당자 변경, 리비전 추가. "
        "clientId와 assigneeId에 고객사/담당자 이름을 넣으면 자동으로 UUID를 조회합니다."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "update", "addRevision"],
                "description": "수행할 액션 (create | update | addRevision)",
            },
            "taskId": {
                "type": "string",
                "description": "태스크 UUID — update/addRevision 시 필수",
            },
            "title": {
                "type": "string",
                "description": "태스크 제목 (create 시 필수, update 시 선택)",
            },
            "clientId": {
                "type": "string",
                "description": "고객사 UUID 또는 이름 (create 시 필수). 이름 전달 시 자동 조회",
            },
            "platforms": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["instagram", "naver_blog", "naver_place", "facebook"],
                },
                "description": "대상 플랫폼 목록 (create 시 필수)",
            },
            "taskType": {
                "type": "string",
                "enum": ["post_writing", "review_reply", "image_creation", "other"],
                "description": "태스크 유형 (create 시 필수)",
            },
            "priority": {
                "type": "string",
                "enum": ["normal", "urgent"],
                "description": "우선순위 (기본값: normal)",
            },
            "dueDate": {
                "type": "string",
                "description": "마감일 ISO 날짜 문자열 (create 시 필수, update 시 선택)",
            },
            "assigneeId": {
                "type": "string",
                "description": "담당자 UUID 또는 이름 (선택). 이름 전달 시 자동 조회",
            },
            "description": {
                "type": "string",
                "description": "태스크 설명 (선택)",
            },
            "revisionAction": {
                "type": "string",
                "enum": [
                    "issued",
                    "acknowledged",
                    "in_progress",
                    "submitted",
                    "under_review",
                    "approved",
                    "rejected",
                    "resubmitted",
                    "delayed",
                    "cancelled",
                ],
                "description": "리비전 액션 (addRevision 시 필수)",
            },
            "contentText": {
                "type": "string",
                "description": "리비전 본문 텍스트 (addRevision, 선택)",
            },
            "mediaUrls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "미디어 URL 목록 (addRevision, 선택)",
            },
            "comment": {
                "type": "string",
                "description": "리비전 코멘트 (addRevision, 선택)",
            },
        },
        "required": ["action"],
    }

    def __init__(self, client: FlowSNSClient):
        self._client = client

    async def _resolve_client_id(self, value: str) -> str:
        """UUID가 아니면 이름으로 고객사를 검색하여 UUID를 반환한다."""
        if _UUID_RE.match(value):
            return value
        clients = await self._client.get("/clients")
        if isinstance(clients, list):
            for c in clients:
                if value in c.get("name", ""):
                    return c["id"]
        raise FlowSNSClientError(404, f"고객사 '{value}'를 찾을 수 없습니다")

    async def _resolve_profile_id(self, value: str) -> str:
        """UUID가 아니면 이름으로 팀원을 검색하여 UUID를 반환한다."""
        if _UUID_RE.match(value):
            return value
        profiles = await self._client.get("/profiles")
        if isinstance(profiles, list):
            for p in profiles:
                if value in p.get("name", ""):
                    return p["id"]
        raise FlowSNSClientError(404, f"담당자 '{value}'를 찾을 수 없습니다")

    async def execute(self, params: dict, context: AgentContext) -> ToolResult:
        company_id = context.metadata.get("company_id")
        if not company_id:
            return ToolResult.fail("company_id not found in context metadata")

        action = params.get("action")

        try:
            if action == "create":
                client_id = await self._resolve_client_id(params["clientId"])
                body: dict[str, Any] = {
                    "title": params["title"],
                    "clientId": client_id,
                    "platforms": params["platforms"],
                    "taskType": params["taskType"],
                    "dueDate": params["dueDate"],
                }
                for key in ("priority", "description"):
                    value = params.get(key)
                    if value is not None:
                        body[key] = value
                assignee = params.get("assigneeId")
                if assignee:
                    body["assigneeId"] = await self._resolve_profile_id(assignee)

                data = await self._client.post("/tasks", json=body)
                return ToolResult.ok(data, tool="flowsns_task_actions", action="create")

            elif action == "update":
                task_id = params.get("taskId")
                if not task_id:
                    return ToolResult.fail("taskId is required for update action")

                body = {}
                for key in ("title", "description", "assigneeId", "priority", "dueDate"):
                    value = params.get(key)
                    if value is not None:
                        body[key] = value

                data = await self._client.patch(f"/tasks/{task_id}", json=body)
                return ToolResult.ok(data, tool="flowsns_task_actions", action="update")

            elif action == "addRevision":
                task_id = params.get("taskId")
                if not task_id:
                    return ToolResult.fail("taskId is required for addRevision action")

                revision_action = params.get("revisionAction")
                if not revision_action:
                    return ToolResult.fail("revisionAction is required for addRevision action")

                body = {"action": revision_action}
                for key in ("contentText", "mediaUrls", "comment"):
                    value = params.get(key)
                    if value is not None:
                        body[key] = value

                data = await self._client.post(f"/tasks/{task_id}/revisions", json=body)
                return ToolResult.ok(data, tool="flowsns_task_actions", action="addRevision")

            else:
                return ToolResult.fail(f"Unknown action: {action!r}. Must be create, update, or addRevision.")

        except FlowSNSClientError as e:
            return ToolResult.fail(
                f"FlowSNS API error: {e.detail}",
                status_code=e.status_code,
            )
