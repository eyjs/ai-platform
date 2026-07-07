"""VLM 재실행 / sync-status 라우트 테스트 (I/F 결함 Fix 3·5).

POST /documents/{id}/vlm-enhance → 202 + job_id (활성 잡 있으면 멱등)
GET  /documents/sync-status/{id} → sync/vlm 잡 상태 + 적재 여부
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.models import UserContext
from src.gateway.router import gateway_router


def _create_test_app(
    *,
    user_role: str = "EDITOR",
    active_job: str | None = None,
    enqueue_result: str | None = None,
    sync_job: dict | None = None,
    vlm_job: dict | None = None,
    document_synced: bool = False,
) -> tuple[FastAPI, AsyncMock]:
    app = FastAPI()
    app.include_router(gateway_router, prefix="/api")

    mock_auth = AsyncMock()
    mock_auth.authenticate = AsyncMock(return_value=UserContext(
        user_id="test-user",
        user_role=user_role,
        security_level_max="INTERNAL",
        allowed_profiles=[],
        allowed_origins=[],
        rate_limit_per_min=60,
    ))
    mock_auth.check_origin = MagicMock()

    mock_job_queue = AsyncMock()
    mock_job_queue.has_active_job = AsyncMock(return_value=active_job)
    mock_job_queue.enqueue = AsyncMock(return_value=enqueue_result or str(uuid.uuid4()))

    async def _latest(queues, document_id):
        return vlm_job if queues == ["vlm_enhance"] else sync_job

    mock_job_queue.get_latest_job_by_document = AsyncMock(side_effect=_latest)

    mock_vector_store = MagicMock()
    mock_vector_store.pool.fetchrow = AsyncMock(
        return_value={"?column?": 1} if document_synced else None,
    )

    mock_rate_limiter = AsyncMock()
    mock_rate_limiter.verify_request = AsyncMock(return_value=None)

    app.state.auth_service = mock_auth
    app.state.job_queue = mock_job_queue
    app.state.vector_store = mock_vector_store
    app.state.rate_limiter = mock_rate_limiter
    app.state.settings = SimpleNamespace(default_tenant_id="default")
    return app, mock_job_queue


class TestVlmRetrigger:
    def test_enqueues_and_returns_202(self):
        job_id = str(uuid.uuid4())
        app, queue = _create_test_app(enqueue_result=job_id)
        resp = TestClient(app).post(
            "/api/documents/doc-1/vlm-enhance", headers={"X-API-Key": "t"},
        )
        assert resp.status_code == 202
        assert resp.json() == {"job_id": job_id, "status": "queued"}
        queue.enqueue.assert_awaited_once_with("vlm_enhance", {"document_id": "doc-1"})

    def test_idempotent_when_active_job_exists(self):
        app, queue = _create_test_app(active_job="existing-job")
        resp = TestClient(app).post(
            "/api/documents/doc-1/vlm-enhance", headers={"X-API-Key": "t"},
        )
        assert resp.status_code == 202
        assert resp.json() == {"job_id": "existing-job", "status": "already_queued"}
        queue.enqueue.assert_not_awaited()

    def test_viewer_forbidden(self):
        app, _ = _create_test_app(user_role="VIEWER")
        resp = TestClient(app).post(
            "/api/documents/doc-1/vlm-enhance", headers={"X-API-Key": "t"},
        )
        assert resp.status_code == 403


class TestSyncStatus:
    def test_returns_job_states(self):
        sync_job = {"job_id": "j1", "queue_name": "kms_sync", "status": "completed",
                    "attempts": 1, "max_attempts": 3, "last_error": None,
                    "created_at": None, "completed_at": None}
        vlm_job = {"job_id": "j2", "queue_name": "vlm_enhance", "status": "failed",
                   "attempts": 3, "max_attempts": 3, "last_error": "boom",
                   "created_at": None, "completed_at": None}
        app, _ = _create_test_app(
            sync_job=sync_job, vlm_job=vlm_job, document_synced=True,
        )
        resp = TestClient(app).get(
            "/api/documents/sync-status/doc-1", headers={"X-API-Key": "t"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sync_job"]["status"] == "completed"
        assert body["vlm_job"]["status"] == "failed"
        assert body["document_synced"] is True

    def test_no_jobs_returns_nulls(self):
        app, _ = _create_test_app()
        resp = TestClient(app).get(
            "/api/documents/sync-status/unknown-doc", headers={"X-API-Key": "t"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sync_job"] is None
        assert body["vlm_job"] is None
        assert body["document_synced"] is False
