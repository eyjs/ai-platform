"""문서 수집 엔드포인트: /documents/ingest, /chat/sessions/{id}/files, /documents/ingest/{job_id}. 인증 필수."""

import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from src.gateway.models import IngestJobStatus, IngestRequest, IngestResponse
from src.gateway.routes.helpers import (
    ROLE_LEVELS,
    _authenticate,
    _check_rate_limit,
    _get_app_state,
    logger,
)

router = APIRouter()


@router.post(
    "/documents/ingest",
    response_model=IngestResponse,
    status_code=202,
)
async def ingest_document(req: IngestRequest, request: Request):
    """문서 수집 요청을 큐에 등록하고 job_id를 즉시 반환한다 (202 Accepted)."""
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx)

    # EDITOR 이상만 문서 수집 가능
    if ROLE_LEVELS.get(user_ctx.user_role, 0) < 1:
        raise HTTPException(
            status_code=403,
            detail="문서 수집은 EDITOR 이상 권한이 필요합니다",
        )

    # 통일된 인제스천 계약 — 바이트 획득 방식 두 가지를 한 진입점에서 분기한다:
    #   (1) 인라인: content 또는 file_base64 가 있으면 'ingest' 큐로 (HTTP·챗봇 업로드)
    #   (2) 참조-fetch: source_document_id 만 있으면 'kms_sync' 큐로 (KMS, 파일은 워커가 fetch)
    tenant_id = user_ctx.tenant_id or state.settings.default_tenant_id
    has_inline = bool(req.content or req.file_base64)

    try:
        if has_inline:
            logger.info(
                "ingest_enqueue",
                title=req.title,
                domain_code=req.domain_code,
                content_len=len(req.content) if req.content else 0,
                has_file=bool(req.file_base64),
                user_id=user_ctx.user_id,
            )
            job_id = await state.job_queue.enqueue(
                queue_name="ingest",
                payload={
                    "title": req.title,
                    "content": req.content,
                    "file_base64": req.file_base64,
                    "domain_code": req.domain_code,
                    "file_name": req.file_name,
                    "security_level": req.security_level,
                    "source_url": req.source_url,
                    "metadata": req.metadata or {},
                    "source_document_id": req.source_document_id,
                    "mime_type": req.mime_type,
                    # 테넌트 격리(A2): 키에 테넌트 없으면 기본 테넌트로 스탬핑
                    "tenant_id": tenant_id,
                },
            )
        elif req.source_document_id:
            # 참조-fetch: 동작하는 KMS sync 경로 재사용 (워커가 KMS에서 파일을 받아 파싱)
            logger.info(
                "ingest_enqueue_source_ref",
                title=req.title,
                domain_code=req.domain_code,
                source_document_id=req.source_document_id,
                source_system=req.source_system or "kms",
                user_id=user_ctx.user_id,
            )
            job_id = await state.job_queue.enqueue(
                queue_name="kms_sync",
                payload={
                    "action": "sync",
                    "document_id": req.source_document_id,
                    "event": "ingest.source_ref",
                    "data": {"domainCodes": [req.domain_code] if req.domain_code else []},
                },
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="content, file_base64, or source_document_id required",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("ingest_enqueue_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    return IngestResponse(job_id=job_id, status="queued")


@router.post("/chat/sessions/{session_id}/files", status_code=202)
async def upload_session_file(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    """챗봇 세션에 파일을 업로드해 적재한다.

    새 진입점이지만 코어(IngestPipeline)·워커 변경 0 — 기존 'ingest' 큐를 그대로
    재사용한다. 업로드 문서는 `metadata.session_id`와 `external_id=session:{id}:{uuid}`
    로 태깅되어, 세션 스코프 검색/만료 정리의 연결점이 된다.
    """
    import base64

    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx)

    # 업로드 문서는 RAG 스토어에 적재된다(세션 스코프 검색은 추후) — 지식베이스
    # 오염을 막기 위해 ingest_document 와 동일하게 EDITOR 이상으로 제한한다.
    if ROLE_LEVELS.get(user_ctx.user_role, 0) < 1:
        raise HTTPException(status_code=403, detail="세션 파일 업로드는 EDITOR 이상 권한이 필요합니다")

    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid session_id: {session_id}")

    tenant_id = user_ctx.tenant_id or state.settings.default_tenant_id

    # 세션 소유권 검증 (IDOR 방지): 세션이 호출자(테넌트+유저)의 것인지 확인한다.
    # 타 세션 주입을 막고, 열거(enumeration) 방지를 위해 미존재/미소유 모두 404.
    session = await state.session_memory.get_session(session_id, tenant_id=tenant_id)
    if not session or (session.get("user_id") and session["user_id"] != user_ctx.user_id):
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="빈 파일입니다")

    external_id = f"session:{session_id}:{uuid.uuid4().hex}"

    logger.info(
        "session_file_upload",
        session_id=session_id,
        file_name=file.filename,
        size=len(file_bytes),
        user_id=user_ctx.user_id,
    )

    try:
        job_id = await state.job_queue.enqueue(
            queue_name="ingest",
            payload={
                "title": file.filename or "uploaded-file",
                "file_base64": base64.b64encode(file_bytes).decode(),
                "domain_code": "",
                "file_name": file.filename,
                "security_level": "PUBLIC",
                "metadata": {"session_id": session_id, "source": "chat_upload"},
                "source_document_id": external_id,
                "mime_type": file.content_type,
                "tenant_id": tenant_id,
            },
        )
    except Exception as e:
        logger.error("session_upload_enqueue_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    # 세션 메타에 업로드 external_id 추적 — 검색 스코핑/만료 정리의 연결점.
    try:
        meta = await state.session_memory.get_orchestrator_metadata(session_id) or {}
        uploads = list(meta.get("uploaded_external_ids", []))
        uploads.append(external_id)
        meta["uploaded_external_ids"] = uploads
        await state.session_memory.save_orchestrator_metadata(session_id, meta)
    except Exception as e:
        logger.warning("session_upload_meta_link_failed", session_id=session_id, error=str(e))

    return {"job_id": job_id, "status": "queued", "external_id": external_id}


@router.post("/documents/{document_id}/vlm-enhance", status_code=202)
async def retrigger_vlm_enhance(document_id: str, request: Request):
    """단일 문서의 VLM 보강을 수동 재실행한다 (I/F 결함 Fix 3).

    기존에는 vlm_enhance 가 kms_sync 내부에서만 큐잉되어 FAILED 후 복구
    수단이 없었다. 같은 문서의 활성 잡이 있으면 그 job_id 를 반환한다(멱등).
    """
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx)

    if ROLE_LEVELS.get(user_ctx.user_role, 0) < 1:
        raise HTTPException(
            status_code=403, detail="VLM 재실행은 EDITOR 이상 권한이 필요합니다",
        )
    if not document_id.strip():
        raise HTTPException(status_code=400, detail="document_id required")

    try:
        existing = await state.job_queue.has_active_job("vlm_enhance", document_id)
        if existing:
            logger.info("vlm_retrigger_dedup", document_id=document_id, job_id=existing)
            return {"job_id": existing, "status": "already_queued"}
        job_id = await state.job_queue.enqueue(
            "vlm_enhance", {"document_id": document_id},
        )
    except Exception as e:
        logger.error("vlm_retrigger_error", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")

    logger.info("vlm_retrigger_enqueued", document_id=document_id, job_id=job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/documents/sync-status/{document_id}")
async def get_document_sync_status(document_id: str, request: Request):
    """문서 기준 동기화/VLM 잡 상태 조회 (KMS 워치독의 정합 확인용, Fix 5).

    KMS 가 PENDING 에 고착된 문서의 실체를 파악한다: 잡이 진행 중인지,
    완료됐는데 콜백이 유실됐는지, 실패/부재인지.
    """
    state = _get_app_state(request)
    await _authenticate(request)

    if not document_id.strip():
        raise HTTPException(status_code=400, detail="document_id required")

    try:
        sync_job = await state.job_queue.get_latest_job_by_document(
            ["kms_sync", "ingest"], document_id,
        )
        vlm_job = await state.job_queue.get_latest_job_by_document(
            ["vlm_enhance"], document_id,
        )
        synced_row = await state.vector_store.pool.fetchrow(
            "SELECT 1 FROM documents WHERE external_id = $1 LIMIT 1", document_id,
        )
    except Exception as e:
        logger.error("sync_status_error", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")

    return {
        "document_id": document_id,
        "sync_job": sync_job,
        "vlm_job": vlm_job,
        "document_synced": synced_row is not None,
    }


@router.get("/documents/ingest/{job_id}", response_model=IngestJobStatus)
async def get_ingest_status(job_id: str, request: Request):
    """문서 수집 작업 상태를 조회한다 (폴링 엔드포인트)."""
    state = _get_app_state(request)
    await _authenticate(request)

    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid job_id format: {job_id}")

    try:
        job = await state.job_queue.get_job(job_id)
    except Exception as e:
        logger.error("ingest_status_error", error=str(e), job_id=job_id)
        raise HTTPException(status_code=500, detail="Internal server error")

    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    # pending -> queued (외부 API 용어 통일)
    status = "queued" if job["status"] == "pending" else job["status"]
    result = job.get("result") if job["status"] == "completed" else None

    return IngestJobStatus(
        job_id=job["id"],
        status=status,
        result=result,
        error=job.get("last_error"),
        attempts=job["attempts"],
        created_at=job.get("created_at"),
    )
