"""KMS Webhook 수신 라우터.

KMS에서 문서 변경 이벤트를 수신하고 비동기 Job 큐에 등록한다.
HMAC-SHA256 서명으로 요청 무결성을 검증한다.
"""

import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request

from src.config import settings
from src.observability.logging import get_logger

logger = get_logger(__name__)

webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# 처리 대상 이벤트
_SYNC_EVENTS = frozenset({
    "document.created",
    "document.updated",
    "document.file_uploaded",
})

_DELETE_EVENTS = frozenset({"document.deleted"})

_LIFECYCLE_EVENTS = frozenset({"document.lifecycle_changed"})


def _verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """HMAC-SHA256 서명을 timing-safe 비교로 검증한다."""
    if not signature_header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


@webhook_router.post("/kms")
async def receive_kms_webhook(request: Request):
    """KMS 문서 변경 webhook을 수신한다. 즉시 200 OK 후 비동기 처리."""
    secret = settings.kms_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature")

    if not _verify_signature(body, signature, secret):
        logger.warning("webhook_signature_invalid")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event", "")
    data = payload.get("data", {})
    document_id = data.get("documentId")

    if not event or not document_id:
        raise HTTPException(status_code=400, detail="Missing event or documentId")

    logger.info(
        "webhook_received",
        event=event,
        document_id=document_id,
        attempt=request.headers.get("X-Webhook-Attempt", "1"),
    )

    state = request.app.state
    job_queue = state.job_queue

    if event in _SYNC_EVENTS:
        await job_queue.enqueue(
            queue_name="kms_sync",
            payload={
                "action": "sync",
                "document_id": document_id,
                "event": event,
                "data": data,
            },
        )
    elif event in _DELETE_EVENTS:
        await job_queue.enqueue(
            queue_name="kms_sync",
            payload={
                "action": "delete",
                "document_id": document_id,
            },
        )
    elif event in _LIFECYCLE_EVENTS:
        await job_queue.enqueue(
            queue_name="kms_sync",
            payload={
                "action": "lifecycle",
                "document_id": document_id,
                "status": data.get("status", ""),
            },
        )
    else:
        logger.info("webhook_event_ignored", event=event)

    return {"status": "accepted"}
