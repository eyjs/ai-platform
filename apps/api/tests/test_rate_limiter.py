"""PostgreSQL Token Bucket Rate Limiter 테스트.

DB 없이 Mock Pool로 acquire/verify 계약과 복합키 축(B5)을 검증한다.
토큰버킷 산식 자체는 SQL(ON CONFLICT UPSERT)로 이전됐으므로 라이브 DB에서 별도 검증.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.gateway.models import UserContext
from src.gateway.rate_limiter import PGRateLimiter, build_client_id
from src.gateway.router import gateway_router


# --- Unit Tests: acquire 계약 (락-프리 UPSERT, B6) ---


class TestTokenBucketAcquire:

    @pytest.fixture
    def mock_pool(self):
        return AsyncMock()

    @pytest.fixture
    def limiter(self, mock_pool):
        return PGRateLimiter(mock_pool)

    @pytest.mark.asyncio
    async def test_allowed_returns_remaining(self, limiter, mock_pool):
        """UPSERT가 행을 반환 → 허용, 남은 토큰 그대로. 추가 조회 없음."""
        mock_pool.fetchrow = AsyncMock(return_value={"tokens": 9.0})

        allowed, remaining = await limiter.acquire("c1", cost=1, capacity=10, refill_rate=1.0)

        assert allowed is True
        assert remaining == 9.0
        mock_pool.fetchval.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_free_uses_upsert_not_for_update(self, limiter, mock_pool):
        """행 락(FOR UPDATE)·명시적 트랜잭션 없이 단일 UPSERT로 처리 (B6)."""
        mock_pool.fetchrow = AsyncMock(return_value={"tokens": 5.0})

        await limiter.acquire("c1", capacity=10, refill_rate=1.0)

        sql = mock_pool.fetchrow.call_args.args[0]
        assert "ON CONFLICT" in sql
        assert "WHERE" in sql
        assert "FOR UPDATE" not in sql
        mock_pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_denied_when_no_row(self, limiter, mock_pool):
        """WHERE 불충족 → 0행 → 거부. fetchval로 현재 토큰 조회."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.fetchval = AsyncMock(return_value=0.3)

        allowed, remaining = await limiter.acquire("c1", cost=1, capacity=10, refill_rate=1.0)

        assert allowed is False
        assert remaining == 0.3

    @pytest.mark.asyncio
    async def test_denied_missing_bucket_defaults_zero(self, limiter, mock_pool):
        """거부 + 버킷 조회도 비면 remaining=0.0."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.fetchval = AsyncMock(return_value=None)

        allowed, remaining = await limiter.acquire("c1")

        assert allowed is False
        assert remaining == 0.0


class TestVerifyRequest:

    @pytest.fixture
    def limiter(self):
        return PGRateLimiter(AsyncMock())

    @pytest.mark.asyncio
    async def test_raises_429_with_retry_after(self, limiter):
        """토큰 소진 시 429 + Retry-After."""
        limiter._pool.fetchrow = AsyncMock(return_value=None)
        limiter._pool.fetchval = AsyncMock(return_value=0.0)

        with pytest.raises(HTTPException) as exc:
            await limiter.verify_request("c1", rate_limit_per_min=60)

        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers
        assert int(exc.value.headers["Retry-After"]) == 1

    @pytest.mark.asyncio
    async def test_passes_when_allowed(self, limiter):
        """토큰 충분하면 통과."""
        limiter._pool.fetchrow = AsyncMock(return_value={"tokens": 30.0})
        await limiter.verify_request("c1", rate_limit_per_min=60)


# --- Unit Tests: 복합키 축 (B5) ---


class TestBuildClientId:

    def test_api_key_is_base(self):
        ctx = UserContext(user_id="api-user", api_key_id="key-123")
        assert build_client_id(ctx) == "key-123"

    def test_composite_with_session(self):
        ctx = UserContext(user_id="api-user", api_key_id="key-123")
        assert build_client_id(ctx, sub_key="sess-9") == "key-123:sess-9"

    def test_jwt_user_uses_user_id(self):
        """JWT 사용자는 api_key_id 없음 → 서명된 user_id가 base."""
        ctx = UserContext(user_id="alice", api_key_id=None)
        assert build_client_id(ctx, sub_key="sess-9") == "alice:sess-9"

    def test_fallback_when_anonymous(self):
        ctx = UserContext(user_id="", api_key_id=None)
        assert build_client_id(ctx, fallback="1.2.3.4") == "1.2.3.4"

    def test_two_sessions_same_key_are_distinct(self):
        """같은 공유키라도 세션이 다르면 다른 버킷 (B5 핵심)."""
        ctx = UserContext(api_key_id="shared-key")
        assert build_client_id(ctx, sub_key="a") != build_client_id(ctx, sub_key="b")

    def test_subkey_length_capped(self):
        """클라이언트 제공 session_id가 길어도 128자로 제한 (PK 비대화 방지)."""
        ctx = UserContext(api_key_id="k")
        cid = build_client_id(ctx, sub_key="s" * 500)
        assert cid == "k:" + "s" * 128


# --- Unit Tests: 유휴 버킷 정리 ---


class TestCleanupStale:

    @pytest.mark.asyncio
    async def test_returns_deleted_count(self):
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="DELETE 7")
        n = await PGRateLimiter(pool).cleanup_stale(idle_seconds=3600)
        assert n == 7

    @pytest.mark.asyncio
    async def test_handles_unexpected_result(self):
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value=None)
        assert await PGRateLimiter(pool).cleanup_stale() == 0


# --- Integration Tests: Router 연동 ---


def _create_test_app(*, rate_limit_allowed: bool = True) -> FastAPI:
    """Rate limiting이 적용된 테스트 앱."""
    app = FastAPI()
    app.include_router(gateway_router, prefix="/api")

    mock_auth = AsyncMock()
    mock_auth.authenticate = AsyncMock(return_value=UserContext(
        user_id="test-user",
        user_role="EDITOR",
        security_level_max="INTERNAL",
        allowed_profiles=[],
        allowed_origins=[],
        rate_limit_per_min=10,
    ))
    mock_auth.check_origin = MagicMock()

    mock_limiter = AsyncMock()
    if rate_limit_allowed:
        mock_limiter.verify_request = AsyncMock(return_value=None)
    else:
        mock_limiter.verify_request = AsyncMock(
            side_effect=HTTPException(status_code=429, detail="Too Many Requests")
        )

    mock_job_queue = AsyncMock()
    mock_job_queue.enqueue = AsyncMock(return_value=str(uuid.uuid4()))

    app.state.auth_service = mock_auth
    app.state.rate_limiter = mock_limiter
    app.state.job_queue = mock_job_queue
    app.state.settings = SimpleNamespace(default_tenant_id="default")

    return app


class TestRateLimitIntegration:

    def test_ingest_passes_when_under_limit(self):
        app = _create_test_app(rate_limit_allowed=True)
        client = TestClient(app)

        resp = client.post("/api/documents/ingest", json={
            "title": "문서",
            "content": "내용",
            "domain_code": "test",
        }, headers={"X-API-Key": "test"})

        assert resp.status_code == 202

    def test_ingest_blocked_when_over_limit(self):
        app = _create_test_app(rate_limit_allowed=False)
        client = TestClient(app)

        resp = client.post("/api/documents/ingest", json={
            "title": "문서",
            "content": "내용",
            "domain_code": "test",
        }, headers={"X-API-Key": "test"})

        assert resp.status_code == 429
        assert "Too Many Requests" in resp.json()["detail"]

    def test_rate_limiter_receives_correct_client_id(self):
        app = _create_test_app(rate_limit_allowed=True)
        client = TestClient(app)

        client.post("/api/documents/ingest", json={
            "title": "문서",
            "content": "내용",
            "domain_code": "test",
        }, headers={"X-API-Key": "test"})

        limiter = app.state.rate_limiter
        limiter.verify_request.assert_called_once()
        call_kwargs = limiter.verify_request.call_args
        assert call_kwargs.kwargs["client_id"] == "test-user"
        assert call_kwargs.kwargs["rate_limit_per_min"] == 10
