"""POST /api/classify-intent 라우트 테스트.

(1) compat 후보 + "그 사람 자꾸 생각나" → intent=compat (분류 LLM 스텁)
(2) 모호 입력 → intent=null, confidence < threshold
(3) candidates 빈 배열 → intent=null
(4) 인증 누락 → 401
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.models import UserContext
from src.gateway.routes.classify import router as classify_router


# ---------------------------------------------------------------------------
# 공통 스텁
# ---------------------------------------------------------------------------

class _StubLLM:
    """generate_json 고정 응답 스텁. SemanticClassifier._classify_with_llm에 사용."""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = 0

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        self.calls += 1
        return self.response


def _make_user_ctx(**kwargs) -> UserContext:
    defaults = dict(
        user_id="test-user",
        user_role="VIEWER",
        security_level_max="PUBLIC",
        allowed_profiles=[],
        allowed_origins=[],
        rate_limit_per_min=60,
    )
    defaults.update(kwargs)
    return UserContext(**defaults)


def _create_app(router_llm=None, auth_fail: bool = False) -> FastAPI:
    """테스트용 FastAPI 앱 (classify_router만 마운트)."""
    app = FastAPI()
    app.include_router(classify_router, prefix="/api")

    # --- app.state mock ---
    mock_state = MagicMock()
    mock_state.router_llm = router_llm

    # auth_service mock
    mock_auth = AsyncMock()
    if auth_fail:
        from src.gateway.auth import AuthError
        mock_auth.authenticate = AsyncMock(side_effect=AuthError("인증 실패"))
    else:
        mock_auth.authenticate = AsyncMock(return_value=_make_user_ctx())
    mock_auth.check_origin = MagicMock()  # sync
    mock_state.auth_service = mock_auth

    # rate_limiter mock
    mock_rl = AsyncMock()
    mock_rl.verify_request = AsyncMock(return_value=None)
    mock_state.rate_limiter = mock_rl

    # request.client mock — build_client_id용
    app.state = mock_state

    # request.client 없이도 동작하도록 미들웨어에서 client 설정
    @app.middleware("http")
    async def _inject_client(request, call_next):
        # TestClient는 request.client를 설정하지만 혹시 모르니 보강
        return await call_next(request)

    return app


# ---------------------------------------------------------------------------
# 테스트 케이스
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compat_candidate_matches():
    """(1) compat 후보 + '그 사람 자꾸 생각나' → intent=compat."""
    stub_llm = _StubLLM({"label": "compat", "confidence": 0.9})
    app = _create_app(router_llm=stub_llm)
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post(
        "/api/classify-intent",
        json={
            "history": [{"role": "user", "content": "안녕"}],
            "message": "그 사람 자꾸 생각나",
            "candidates": [
                {"label": "compat", "description": "연애·궁합 관련 질문"},
                {"label": "self_reading", "description": "본인 사주 해석"},
            ],
            "threshold": 0.6,
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "compat"
    assert data["confidence"] >= 0.6
    assert stub_llm.calls == 1


@pytest.mark.asyncio
async def test_ambiguous_returns_null():
    """(2) 모호 입력 → intent=null, confidence < threshold."""
    stub_llm = _StubLLM({"label": "compat", "confidence": 0.2})  # threshold 미만
    app = _create_app(router_llm=stub_llm)
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post(
        "/api/classify-intent",
        json={
            "message": "음...",
            "candidates": [
                {"label": "compat"},
                {"label": "career_timing"},
            ],
            "threshold": 0.6,
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] is None
    assert data["confidence"] < 0.6


@pytest.mark.asyncio
async def test_empty_candidates_returns_null():
    """(3) candidates 빈 배열 → intent=null, confidence=0."""
    stub_llm = _StubLLM({"label": "compat", "confidence": 0.99})
    app = _create_app(router_llm=stub_llm)
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post(
        "/api/classify-intent",
        json={
            "message": "오늘 운세 어때",
            "candidates": [],
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] is None
    assert data["confidence"] == 0.0
    # LLM은 호출되지 않아야 함 (fast-exit)
    assert stub_llm.calls == 0


@pytest.mark.asyncio
async def test_missing_auth_returns_401():
    """(4) 인증 누락(헤더 없음) → 401."""
    app = _create_app(auth_fail=True)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/classify-intent",
        json={
            "message": "테스트",
            "candidates": [{"label": "compat"}],
        },
        # X-API-Key 헤더 없음 → auth_fail=True 스텁이 AuthError 발생
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_history_as_string():
    """history가 문자열로 전달될 때도 동작한다."""
    stub_llm = _StubLLM({"label": "self_reading", "confidence": 0.8})
    app = _create_app(router_llm=stub_llm)
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post(
        "/api/classify-intent",
        json={
            "history": "이전 대화 내용",
            "message": "내 사주 좀 봐줘",
            "candidates": [
                {"label": "self_reading", "description": "본인 사주 해석"},
            ],
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "self_reading"


@pytest.mark.asyncio
async def test_no_llm_fastpath_exact_match():
    """router_llm=None 시 정확 라벨 입력은 fast-path로 매칭된다."""
    app = _create_app(router_llm=None)
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post(
        "/api/classify-intent",
        json={
            "message": "compat",
            "candidates": [
                {"label": "compat"},
                {"label": "career_timing"},
            ],
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "compat"
    assert data["confidence"] == 1.0


@pytest.mark.asyncio
async def test_no_llm_freetext_returns_null():
    """router_llm=None 시 자유입력은 null 반환 (하위호환)."""
    app = _create_app(router_llm=None)
    client = TestClient(app, raise_server_exceptions=True)

    resp = client.post(
        "/api/classify-intent",
        json={
            "message": "그냥 오늘 심심해서",
            "candidates": [{"label": "compat"}],
        },
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] is None
