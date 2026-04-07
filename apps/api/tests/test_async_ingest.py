"""비동기 문서 수집 파이프라인 테스트.

POST /documents/ingest → 202 + job_id 즉시 반환
GET /documents/ingest/{job_id} → 작업 상태 폴링
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.models import UserContext
from src.gateway.router import gateway_router


def _create_test_app(*, enqueue_result: str = None, get_job_result: dict = None) -> FastAPI:
    """테스트용 FastAPI 앱을 생성한다."""
    app = FastAPI()
    app.include_router(gateway_router, prefix="/api")

    # Mock auth (EDITOR 권한)
    mock_auth = AsyncMock()
    mock_auth.authenticate = AsyncMock(return_value=UserContext(
        user_id="test-editor",
        user_role="EDITOR",
        security_level_max="INTERNAL",
        allowed_profiles=[],
        allowed_origins=[],
        rate_limit_per_min=60,
    ))
    mock_auth.check_origin = MagicMock()

    # Mock JobQueue
    mock_job_queue = AsyncMock()
    job_id = enqueue_result or str(uuid.uuid4())
    mock_job_queue.enqueue = AsyncMock(return_value=job_id)
    mock_job_queue.get_job = AsyncMock(return_value=get_job_result)

    mock_rate_limiter = AsyncMock()
    mock_rate_limiter.verify_request = AsyncMock(return_value=None)

    app.state.auth_service = mock_auth
    app.state.job_queue = mock_job_queue
    app.state.rate_limiter = mock_rate_limiter

    return app


@pytest.fixture
def job_id():
    return str(uuid.uuid4())


class TestIngestEnqueue:

    def test_returns_202_with_job_id(self, job_id):
        app = _create_test_app(enqueue_result=job_id)
        client = TestClient(app)

        resp = client.post("/api/documents/ingest", json={
            "title": "테스트 문서",
            "content": "문서 내용입니다.",
            "domain_code": "test/domain",
        }, headers={"X-API-Key": "test"})

        assert resp.status_code == 202
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["status"] == "queued"

    def test_enqueue_payload_matches_request(self, job_id):
        app = _create_test_app(enqueue_result=job_id)
        client = TestClient(app)

        req_body = {
            "title": "보험 약관",
            "content": "제1조 목적",
            "domain_code": "insurance/auto",
            "file_name": "policy.md",
            "security_level": "INTERNAL",
            "metadata": {"insurer": "삼성화재"},
        }
        client.post("/api/documents/ingest", json=req_body, headers={"X-API-Key": "test"})

        mock_queue = app.state.job_queue
        mock_queue.enqueue.assert_called_once()
        call_kwargs = mock_queue.enqueue.call_args
        assert call_kwargs.kwargs["queue_name"] == "ingest"
        payload = call_kwargs.kwargs["payload"]
        assert payload["title"] == "보험 약관"
        assert payload["domain_code"] == "insurance/auto"
        assert payload["file_name"] == "policy.md"
        assert payload["security_level"] == "INTERNAL"
        assert payload["metadata"] == {"insurer": "삼성화재"}

    def test_missing_content_and_url_returns_400(self):
        app = _create_test_app()
        client = TestClient(app)

        resp = client.post("/api/documents/ingest", json={
            "title": "빈 문서",
            "domain_code": "test",
        }, headers={"X-API-Key": "test"})

        assert resp.status_code == 400

    def test_viewer_forbidden(self):
        app = _create_test_app()
        app.state.auth_service.authenticate = AsyncMock(return_value=UserContext(
            user_id="test-viewer",
            user_role="VIEWER",
        ))
        client = TestClient(app)

        resp = client.post("/api/documents/ingest", json={
            "title": "문서",
            "content": "내용",
            "domain_code": "test",
        }, headers={"X-API-Key": "test"})

        assert resp.status_code == 403


class TestIngestStatus:

    def test_pending_job_returns_queued(self, job_id):
        job_data = {
            "id": job_id,
            "queue_name": "ingest",
            "payload": {"title": "테스트"},
            "status": "pending",
            "attempts": 0,
            "max_attempts": 3,
            "last_error": None,
            "result": None,
            "created_at": "2026-03-13T10:00:00+00:00",
            "completed_at": None,
        }
        app = _create_test_app(get_job_result=job_data)
        client = TestClient(app)

        resp = client.get(f"/api/documents/ingest/{job_id}", headers={"X-API-Key": "test"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["status"] == "queued"  # pending → queued 변환
        assert data["result"] is None

    def test_processing_job(self, job_id):
        job_data = {
            "id": job_id,
            "queue_name": "ingest",
            "payload": {"title": "테스트"},
            "status": "processing",
            "attempts": 1,
            "max_attempts": 3,
            "last_error": None,
            "result": None,
            "created_at": "2026-03-13T10:00:00+00:00",
            "completed_at": None,
        }
        app = _create_test_app(get_job_result=job_data)
        client = TestClient(app)

        resp = client.get(f"/api/documents/ingest/{job_id}", headers={"X-API-Key": "test"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"

    def test_completed_job_includes_result(self, job_id):
        job_data = {
            "id": job_id,
            "queue_name": "ingest",
            "payload": {"title": "완료된 문서"},
            "status": "completed",
            "attempts": 1,
            "max_attempts": 3,
            "last_error": None,
            "result": {"document_id": "doc-123", "title": "완료된 문서", "chunks": 5, "status": "success"},
            "created_at": "2026-03-13T10:00:00+00:00",
            "completed_at": "2026-03-13T10:00:05+00:00",
        }
        app = _create_test_app(get_job_result=job_data)
        client = TestClient(app)

        resp = client.get(f"/api/documents/ingest/{job_id}", headers={"X-API-Key": "test"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["result"] is not None
        assert data["result"]["document_id"] == "doc-123"
        assert data["result"]["chunks"] == 5

    def test_failed_job_includes_error(self, job_id):
        job_data = {
            "id": job_id,
            "queue_name": "ingest",
            "payload": {"title": "실패 문서"},
            "status": "failed",
            "attempts": 3,
            "max_attempts": 3,
            "last_error": "Embedding service unavailable",
            "result": None,
            "created_at": "2026-03-13T10:00:00+00:00",
            "completed_at": "2026-03-13T10:01:00+00:00",
        }
        app = _create_test_app(get_job_result=job_data)
        client = TestClient(app)

        resp = client.get(f"/api/documents/ingest/{job_id}", headers={"X-API-Key": "test"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error"] == "Embedding service unavailable"
        assert data["attempts"] == 3

    def test_nonexistent_job_returns_404(self, job_id):
        app = _create_test_app(get_job_result=None)
        client = TestClient(app)

        resp = client.get(f"/api/documents/ingest/{job_id}", headers={"X-API-Key": "test"})

        assert resp.status_code == 404

    def test_invalid_uuid_returns_400(self):
        app = _create_test_app(get_job_result=None)
        client = TestClient(app)

        resp = client.get("/api/documents/ingest/not-a-uuid", headers={"X-API-Key": "test"})

        assert resp.status_code == 400
        assert "Invalid job_id format" in resp.json()["detail"]
