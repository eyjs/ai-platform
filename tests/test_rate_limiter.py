"""PostgreSQL Token Bucket Rate Limiter 테스트.

DB 없이 Mock Pool로 Token Bucket 알고리즘을 검증한다.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.gateway.models import UserContext
from src.gateway.rate_limiter import PGRateLimiter
from src.gateway.router import gateway_router


# --- Unit Tests: Token Bucket 알고리즘 ---


class TestTokenBucketAlgorithm:

    @pytest.fixture
    def mock_pool(self):
        pool = AsyncMock()
        return pool

    @pytest.fixture
    def limiter(self, mock_pool):
        return PGRateLimiter(mock_pool)

    def _mock_conn(self, mock_pool, fetchrow_result=None):
        """트랜잭션 컨텍스트 매니저를 모킹한다."""
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=fetchrow_result)
        conn.execute = AsyncMock()

        # async with pool.acquire() as conn: / async with conn.transaction():
        tx_ctx = AsyncMock()
        tx_ctx.__aenter__ = AsyncMock(return_value=None)
        tx_ctx.__aexit__ = AsyncMock(return_value=None)
        conn.transaction = MagicMock(return_value=tx_ctx)

        acq_ctx = AsyncMock()
        acq_ctx.__aenter__ = AsyncMock(return_value=conn)
        acq_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire = MagicMock(return_value=acq_ctx)

        return conn

    @pytest.mark.asyncio
    async def test_first_request_creates_bucket(self, limiter, mock_pool):
        """최초 요청 시 버킷을 생성하고 토큰을 차감한다."""
        conn = self._mock_conn(mock_pool, fetchrow_result=None)

        allowed, remaining = await limiter.acquire("client-1", cost=1, capacity=10, refill_rate=1.0)

        assert allowed is True
        assert remaining == 9.0
        conn.execute.assert_called_once()  # INSERT

    @pytest.mark.asyncio
    async def test_tokens_depleted_rejects(self, limiter, mock_pool):
        """토큰이 부족하면 거부한다."""
        conn = self._mock_conn(mock_pool, fetchrow_result={
            "tokens": 0.5,
            "elapsed": 0.0,  # 충전 없음
        })

        allowed, remaining = await limiter.acquire("client-1", cost=1, capacity=10, refill_rate=1.0)

        assert allowed is False
        assert remaining == 0.5

    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self, limiter, mock_pool):
        """시간이 지나면 토큰이 충전된다."""
        conn = self._mock_conn(mock_pool, fetchrow_result={
            "tokens": 0.0,
            "elapsed": 5.0,  # 5초 경과 → 5개 충전
        })

        allowed, remaining = await limiter.acquire("client-1", cost=1, capacity=10, refill_rate=1.0)

        assert allowed is True
        assert remaining == 4.0  # 5 충전 - 1 소비

    @pytest.mark.asyncio
    async def test_refill_capped_at_capacity(self, limiter, mock_pool):
        """충전량이 capacity를 초과하지 않는다."""
        conn = self._mock_conn(mock_pool, fetchrow_result={
            "tokens": 8.0,
            "elapsed": 100.0,  # 100초 → 100개 충전하지만 capacity=10 제한
        })

        allowed, remaining = await limiter.acquire("client-1", cost=1, capacity=10, refill_rate=1.0)

        assert allowed is True
        assert remaining == 9.0  # min(10, 8+100) - 1 = 9

    @pytest.mark.asyncio
    async def test_custom_cost(self, limiter, mock_pool):
        """cost가 높은 요청은 토큰을 더 많이 소비한다."""
        conn = self._mock_conn(mock_pool, fetchrow_result={
            "tokens": 5.0,
            "elapsed": 0.0,
        })

        allowed, remaining = await limiter.acquire("client-1", cost=3, capacity=10, refill_rate=1.0)

        assert allowed is True
        assert remaining == 2.0

    @pytest.mark.asyncio
    async def test_verify_request_raises_429(self, limiter, mock_pool):
        """토큰 소진 시 429를 반환한다."""
        self._mock_conn(mock_pool, fetchrow_result={
            "tokens": 0.0,
            "elapsed": 0.0,
        })

        with pytest.raises(HTTPException) as exc_info:
            await limiter.verify_request("client-1", rate_limit_per_min=60)

        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_verify_request_passes(self, limiter, mock_pool):
        """토큰 충분하면 통과한다."""
        self._mock_conn(mock_pool, fetchrow_result={
            "tokens": 30.0,
            "elapsed": 0.0,
        })

        # 예외 없이 통과해야 함
        await limiter.verify_request("client-1", rate_limit_per_min=60)


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
