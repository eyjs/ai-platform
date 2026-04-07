"""Admin API: Profile + Workflow CRUD.

관리자가 코드 배포 없이 챗봇 설정과 워크플로우를 관리하는 REST API.
모든 엔드포인트는 ADMIN 권한의 JWT/API Key를 요구한다.
"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import httpx

from src.domain.models import UserRole
from src.gateway.auth import AuthError
from src.gateway.models import UserContext
from src.observability.logging import get_logger

logger = get_logger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["admin"])


# --- Request/Response Models ---


# 공통 Enum Literal 타입
ModeType = Literal["deterministic", "agentic", "workflow", "hybrid"]
SecurityLevelType = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"]
ResponsePolicyType = Literal["strict", "balanced"]
EscapePolicyType = Literal["allow", "block", "queue"]
MemoryType = Literal["short", "session", "long"]


class ProfileCreateRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    mode: ModeType = "agentic"
    system_prompt: str = ""
    domain_scopes: list[str] = []
    category_scopes: list[str] = []
    security_level_max: SecurityLevelType = "PUBLIC"
    include_common: bool = True
    workflow_id: str | None = None
    hybrid_triggers: list[dict] = []
    tools: list[dict] = []
    response_policy: ResponsePolicyType = "balanced"
    guardrails: list[str] = []
    router_model: str = "haiku"
    main_model: str = "sonnet"
    memory_type: MemoryType = "short"
    memory_ttl_seconds: int = Field(3600, ge=60, le=86400)
    max_tool_calls: int = Field(5, ge=1, le=20)
    agent_timeout_seconds: int = Field(30, ge=5, le=300)
    intent_hints: list[dict] = []


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    mode: ModeType | None = None
    system_prompt: str | None = None
    domain_scopes: list[str] | None = None
    category_scopes: list[str] | None = None
    security_level_max: SecurityLevelType | None = None
    include_common: bool | None = None
    workflow_id: str | None = None
    hybrid_triggers: list[dict] | None = None
    tools: list[dict] | None = None
    response_policy: ResponsePolicyType | None = None
    guardrails: list[str] | None = None
    router_model: str | None = None
    main_model: str | None = None
    memory_type: MemoryType | None = None
    memory_ttl_seconds: int | None = Field(None, ge=60, le=86400)
    max_tool_calls: int | None = Field(None, ge=1, le=20)
    agent_timeout_seconds: int | None = Field(None, ge=5, le=300)
    intent_hints: list[dict] | None = None


class WorkflowStepModel(BaseModel):
    id: str
    type: str = "message"
    prompt: str = ""
    save_as: str = ""
    options: list[str] = []
    branches: dict[str, str] = {}
    next: str | None = None
    tool: str | None = None
    tool_params: dict = {}
    validation: str = ""


class WorkflowCreateRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    steps: list[WorkflowStepModel] = []
    escape_policy: EscapePolicyType = "allow"
    max_retries: int = Field(3, ge=1, le=10)
    first_step: str = ""


class WorkflowUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    steps: list[WorkflowStepModel] | None = None
    escape_policy: EscapePolicyType | None = None
    max_retries: int | None = Field(None, ge=1, le=10)
    first_step: str | None = None


class TenantCreateRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    orchestrator_enabled: bool = True
    default_chatbot_id: str | None = None


class TenantUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    orchestrator_enabled: bool | None = None
    default_chatbot_id: str | None = None


class TenantProfilesRequest(BaseModel):
    profile_ids: list[str]


# --- Helpers ---


def _get_app_state(request: Request):
    return request.app.state


async def _require_admin(request: Request) -> UserContext:
    """ADMIN 권한 + Origin 검증을 요구한다."""
    state = _get_app_state(request)
    try:
        user_ctx = await state.auth_service.authenticate(
            authorization=request.headers.get("Authorization"),
            api_key=request.headers.get("X-API-Key"),
        )
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    # Origin 화이트리스트 검증
    try:
        state.auth_service.check_origin(
            user_ctx,
            origin=request.headers.get("Origin"),
        )
    except AuthError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if user_ctx.user_role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="ADMIN 권한이 필요합니다")

    return user_ctx


# --- Profile CRUD ---


@admin_router.get("/profiles")
async def list_profiles(request: Request):
    """모든 활성 프로필 목록."""
    await _require_admin(request)
    state = _get_app_state(request)
    profiles = await state.profile_store.list_all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "mode": p.mode.value,
            "domain_scopes": p.domain_scopes,
            "tools": [{"name": t.name, "config": t.config} for t in p.tools],
            "system_prompt": p.system_prompt[:200] + ("..." if len(p.system_prompt) > 200 else ""),
            "response_policy": p.response_policy,
            "workflow_id": p.workflow_id,
        }
        for p in profiles
    ]


@admin_router.get("/profiles/{profile_id}")
async def get_profile(profile_id: str, request: Request):
    """프로필 상세 조회."""
    await _require_admin(request)
    state = _get_app_state(request)
    profile = await state.profile_store.get(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"프로필을 찾을 수 없습니다: {profile_id}")

    return _profile_to_response(profile)


@admin_router.post("/profiles", status_code=201)
async def create_profile(req: ProfileCreateRequest, request: Request):
    """프로필 생성."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    existing = await state.profile_store.get(req.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"이미 존재하는 프로필입니다: {req.id}")

    profile = state.profile_store.parse_profile(req.model_dump())
    await state.profile_store.create(profile)

    logger.info("admin_profile_created", profile_id=req.id, by=user_ctx.user_id)
    return _profile_to_response(profile)


@admin_router.put("/profiles/{profile_id}")
async def update_profile(profile_id: str, req: ProfileUpdateRequest, request: Request):
    """프로필 업데이트 (부분 수정)."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    existing = await state.profile_store.get(profile_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"프로필을 찾을 수 없습니다: {profile_id}")

    # 기존 프로필을 dict로 변환 → 변경 필드만 덮어쓰기
    merged = state.profile_store.profile_to_dict(existing)

    updates = req.model_dump(exclude_none=True)
    merged.update(updates)

    profile = state.profile_store.parse_profile(merged)
    await state.profile_store.update(profile)

    logger.info("admin_profile_updated", profile_id=profile_id, by=user_ctx.user_id, fields=list(updates.keys()))
    return _profile_to_response(profile)


@admin_router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str, request: Request):
    """프로필 비활성화 (soft delete)."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    deleted = await state.profile_store.delete(profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"프로필을 찾을 수 없습니다: {profile_id}")

    logger.info("admin_profile_deleted", profile_id=profile_id, by=user_ctx.user_id)
    return {"status": "deleted", "id": profile_id}


# --- Workflow CRUD ---


@admin_router.get("/workflows")
async def list_workflows(request: Request):
    """모든 활성 워크플로우 목록."""
    await _require_admin(request)
    state = _get_app_state(request)
    workflows = await state.workflow_store.list_all_async()
    return [
        {
            "id": w.id,
            "name": w.name,
            "description": w.description,
            "steps_count": len(w.steps),
            "escape_policy": w.escape_policy,
            "max_retries": w.max_retries,
        }
        for w in workflows
    ]


@admin_router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, request: Request):
    """워크플로우 상세 조회."""
    await _require_admin(request)
    state = _get_app_state(request)
    workflow = await state.workflow_store.get_async(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail=f"워크플로우를 찾을 수 없습니다: {workflow_id}")

    return _workflow_to_response(workflow)


@admin_router.post("/workflows", status_code=201)
async def create_workflow(req: WorkflowCreateRequest, request: Request):
    """워크플로우 생성."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    existing = await state.workflow_store.get_async(req.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"이미 존재하는 워크플로우입니다: {req.id}")

    from src.workflow.definition import WorkflowDefinition, WorkflowStep

    steps = [
        WorkflowStep(**step.model_dump())
        for step in req.steps
    ]
    definition = WorkflowDefinition(
        id=req.id,
        name=req.name,
        description=req.description,
        first_step=req.first_step,
        steps=steps,
        escape_policy=req.escape_policy,
        max_retries=req.max_retries,
    )

    await state.workflow_store.create(definition)

    logger.info("admin_workflow_created", workflow_id=req.id, steps=len(steps), by=user_ctx.user_id)
    return _workflow_to_response(definition)


@admin_router.put("/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, req: WorkflowUpdateRequest, request: Request):
    """워크플로우 업데이트 (부분 수정)."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    existing = await state.workflow_store.get_async(workflow_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"워크플로우를 찾을 수 없습니다: {workflow_id}")

    from src.workflow.definition import WorkflowDefinition, WorkflowStep

    name = req.name if req.name is not None else existing.name
    description = req.description if req.description is not None else existing.description
    escape_policy = req.escape_policy if req.escape_policy is not None else existing.escape_policy
    max_retries = req.max_retries if req.max_retries is not None else existing.max_retries
    first_step = req.first_step if req.first_step is not None else existing.first_step

    if req.steps is not None:
        steps = [WorkflowStep(**s.model_dump()) for s in req.steps]
    else:
        steps = list(existing.steps)

    definition = WorkflowDefinition(
        id=workflow_id,
        name=name,
        description=description,
        first_step=first_step,
        steps=steps,
        escape_policy=escape_policy,
        max_retries=max_retries,
    )

    await state.workflow_store.update(definition)

    updates = req.model_dump(exclude_none=True)
    logger.info("admin_workflow_updated", workflow_id=workflow_id, by=user_ctx.user_id, fields=list(updates.keys()))
    return _workflow_to_response(definition)


@admin_router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, request: Request):
    """워크플로우 비활성화 (soft delete)."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    deleted = await state.workflow_store.delete(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"워크플로우를 찾을 수 없습니다: {workflow_id}")

    logger.info("admin_workflow_deleted", workflow_id=workflow_id, by=user_ctx.user_id)
    return {"status": "deleted", "id": workflow_id}


# --- Tenant CRUD ---


@admin_router.get("/tenants")
async def list_tenants(request: Request):
    """모든 활성 테넌트 목록."""
    await _require_admin(request)
    state = _get_app_state(request)
    tenants = await state.tenant_service.list_all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "orchestrator_enabled": t.orchestrator_enabled,
            "default_chatbot_id": t.default_chatbot_id,
            "is_active": t.is_active,
        }
        for t in tenants
    ]


@admin_router.get("/tenants/{tenant_id}")
async def get_tenant(tenant_id: str, request: Request):
    """테넌트 상세 조회."""
    await _require_admin(request)
    state = _get_app_state(request)
    tenant = await state.tenant_service.get(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"테넌트를 찾을 수 없습니다: {tenant_id}")

    profiles = await state.tenant_service.get_allowed_profiles(tenant_id)
    return {
        "id": tenant.id,
        "name": tenant.name,
        "orchestrator_enabled": tenant.orchestrator_enabled,
        "default_chatbot_id": tenant.default_chatbot_id,
        "is_active": tenant.is_active,
        "profiles": profiles,
    }


@admin_router.post("/tenants", status_code=201)
async def create_tenant(req: TenantCreateRequest, request: Request):
    """테넌트 생성."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    existing = await state.tenant_service.get(req.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"이미 존재하는 테넌트입니다: {req.id}")

    tenant = await state.tenant_service.create(
        tenant_id=req.id,
        name=req.name,
        description=req.description,
        orchestrator_enabled=req.orchestrator_enabled,
        default_chatbot_id=req.default_chatbot_id,
    )
    logger.info("admin_tenant_created", tenant_id=req.id, by=user_ctx.user_id)
    return {
        "id": tenant.id,
        "name": tenant.name,
        "orchestrator_enabled": tenant.orchestrator_enabled,
        "default_chatbot_id": tenant.default_chatbot_id,
    }


@admin_router.put("/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, req: TenantUpdateRequest, request: Request):
    """테넌트 업데이트 (부분 수정)."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    existing = await state.tenant_service.get(tenant_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"테넌트를 찾을 수 없습니다: {tenant_id}")

    updated = await state.tenant_service.update(
        tenant_id=tenant_id,
        name=req.name,
        description=req.description,
        orchestrator_enabled=req.orchestrator_enabled,
        default_chatbot_id=req.default_chatbot_id,
    )
    if not updated:
        raise HTTPException(status_code=400, detail="변경 사항이 없습니다")

    logger.info("admin_tenant_updated", tenant_id=tenant_id, by=user_ctx.user_id)
    return {"status": "updated", "id": tenant_id}


@admin_router.delete("/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str, request: Request):
    """테넌트 비활성화 (soft delete)."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    deleted = await state.tenant_service.deactivate(tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"테넌트를 찾을 수 없습니다: {tenant_id}")

    logger.info("admin_tenant_deleted", tenant_id=tenant_id, by=user_ctx.user_id)
    return {"status": "deleted", "id": tenant_id}


@admin_router.put("/tenants/{tenant_id}/profiles")
async def set_tenant_profiles(tenant_id: str, req: TenantProfilesRequest, request: Request):
    """테넌트 프로필 할당 (전체 교체)."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    existing = await state.tenant_service.get(tenant_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"테넌트를 찾을 수 없습니다: {tenant_id}")

    await state.tenant_service.set_profiles(tenant_id, req.profile_ids)
    logger.info(
        "admin_tenant_profiles_set",
        tenant_id=tenant_id,
        count=len(req.profile_ids),
        by=user_ctx.user_id,
    )
    return {"status": "updated", "tenant_id": tenant_id, "profiles": req.profile_ids}


@admin_router.post("/tenants/{tenant_id}/profiles/{profile_id}", status_code=201)
async def add_tenant_profile(tenant_id: str, profile_id: str, request: Request):
    """테넌트에 프로필 추가."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    existing = await state.tenant_service.get(tenant_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"테넌트를 찾을 수 없습니다: {tenant_id}")

    await state.tenant_service.add_profile(tenant_id, profile_id)
    logger.info(
        "admin_tenant_profile_added",
        tenant_id=tenant_id,
        profile_id=profile_id,
        by=user_ctx.user_id,
    )
    return {"status": "added", "tenant_id": tenant_id, "profile_id": profile_id}


@admin_router.delete("/tenants/{tenant_id}/profiles/{profile_id}")
async def remove_tenant_profile(tenant_id: str, profile_id: str, request: Request):
    """테넌트에서 프로필 제거."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    removed = await state.tenant_service.remove_profile(tenant_id, profile_id)
    if not removed:
        raise HTTPException(status_code=404, detail="해당 프로필이 테넌트에 없습니다")

    logger.info(
        "admin_tenant_profile_removed",
        tenant_id=tenant_id,
        profile_id=profile_id,
        by=user_ctx.user_id,
    )
    return {"status": "removed", "tenant_id": tenant_id, "profile_id": profile_id}


# --- Cache Management ---


@admin_router.post("/cache/invalidate")
async def invalidate_cache(request: Request):
    """프로필 + 워크플로우 캐시를 전체 무효화한다."""
    user_ctx = await _require_admin(request)
    state = _get_app_state(request)

    state.profile_store.invalidate_cache()
    state.workflow_store.invalidate_cache()

    logger.info("admin_cache_invalidated", by=user_ctx.user_id)
    return {"status": "ok", "message": "모든 캐시가 무효화되었습니다"}


# --- KMS Proxy ---


@admin_router.get("/kms/domains")
async def list_kms_domains(request: Request):
    """KMS에서 도메인 목록을 프록시 조회한다."""
    await _require_admin(request)

    from src.config import settings

    if not settings.kms_api_url or not settings.kms_internal_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.kms_api_url}/domains",
                headers={"X-Internal-Key": settings.kms_internal_key},
            )
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, OSError) as e:
        logger.warning("kms_proxy_failed", error=str(e))
        return []


# --- Response Helpers ---


def _profile_to_response(profile: Any) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "mode": profile.mode.value,
        "domain_scopes": profile.domain_scopes,
        "category_scopes": profile.category_scopes,
        "security_level_max": profile.security_level_max,
        "include_common": profile.include_common,
        "workflow_id": profile.workflow_id,
        "hybrid_triggers": [
            {"keyword_patterns": t.keyword_patterns, "intent_types": t.intent_types, "workflow_id": t.workflow_id}
            for t in profile.hybrid_triggers
        ],
        "tools": [{"name": t.name, "config": t.config} for t in profile.tools],
        "system_prompt": profile.system_prompt,
        "response_policy": profile.response_policy,
        "guardrails": profile.guardrails,
        "router_model": profile.router_model,
        "main_model": profile.main_model,
        "memory_type": profile.memory_type,
        "memory_ttl_seconds": profile.memory_ttl_seconds,
        "max_tool_calls": profile.max_tool_calls,
        "agent_timeout_seconds": profile.agent_timeout_seconds,
        "intent_hints": [
            {"name": h.name, "patterns": h.patterns, "description": h.description}
            for h in profile.intent_hints
        ],
    }


def _workflow_to_response(workflow: Any) -> dict:
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "first_step": workflow.first_step,
        "escape_policy": workflow.escape_policy,
        "max_retries": workflow.max_retries,
        "steps": [
            {
                "id": s.id,
                "type": s.type,
                "prompt": s.prompt,
                "save_as": s.save_as,
                "options": s.options,
                "branches": s.branches,
                "next": s.next,
                "tool": s.tool,
                "tool_params": s.tool_params,
                "validation": s.validation,
            }
            for s in workflow.steps
        ],
    }
