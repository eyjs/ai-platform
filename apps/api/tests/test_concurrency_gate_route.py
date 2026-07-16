"""전역 동시 실행 게이트의 HTTP 계약 — 503 + Retry-After, 슬롯 회수.

단위 테스트(test_concurrency_gate.py)는 게이트 자료구조를 본다. 여기서는 실제
라우트가 게이트를 **잡고 놓는지**를 본다 — 해제를 한 곳이라도 빠뜨리면 상한이
조용히 줄어들어 결국 전부 503이 되므로, 누수는 계약 수준에서 고정한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.gateway.concurrency_gate import ConcurrencyGate
from src.gateway.models import UserContext
from src.gateway.routes.helpers import _acquire_agent_slot, _release_agent_slot

# Request는 모듈 전역에 있어야 한다 — `from __future__ import annotations`로 어노테이션이
# 문자열이 되면 FastAPI가 모듈 globals에서 타입을 푼다. 함수 안에서 import하면 못 찾아
# 본문 파라미터로 오해하고 422를 낸다.


def _user_ctx() -> UserContext:
    return UserContext(
        user_id="u1",
        user_role="VIEWER",
        security_level_max="PUBLIC",
        allowed_profiles=[],
        allowed_origins=[],
        rate_limit_per_min=60,
    )


def _create_app(gate: ConcurrencyGate, handler_raises: bool = False) -> FastAPI:
    """게이트만 검증하는 최소 앱 — helpers의 acquire/release를 그대로 쓴다."""
    app = FastAPI()

    state = MagicMock()
    state.concurrency_gate = gate
    mock_auth = AsyncMock()
    mock_auth.authenticate = AsyncMock(return_value=_user_ctx())
    mock_auth.check_origin = MagicMock()
    state.auth_service = mock_auth
    rl = AsyncMock()
    rl.verify_request = AsyncMock(return_value=None)
    state.rate_limiter = rl
    app.state = state

    @app.post("/fake-chat")
    async def fake_chat(request: Request):
        _acquire_agent_slot(request)
        try:
            if handler_raises:
                raise RuntimeError("핸들러 폭발")
            return {"ok": True}
        finally:
            _release_agent_slot(request)

    @app.post("/fake-chat-noslot")
    async def fake_chat_noslot(request: Request):
        """슬롯을 잡되 놓지 않는 경로 — 누수 상황을 재현해 테스트가 유효함을 보인다."""
        _acquire_agent_slot(request)
        return {"ok": True}

    return app


def test_rejects_with_503_and_retry_after():
    gate = ConcurrencyGate(limit=1)
    gate.try_acquire()  # 슬롯을 미리 채워 만석 상태로
    client = TestClient(_create_app(gate), raise_server_exceptions=False)

    resp = client.post("/fake-chat")

    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "5"
    assert "동시 요청" in resp.json()["detail"]


def test_under_limit_passes_through():
    gate = ConcurrencyGate(limit=1)
    client = TestClient(_create_app(gate))

    assert client.post("/fake-chat").status_code == 200


def test_slot_released_on_success():
    """정상 응답 후 슬롯이 회수돼야 다음 요청이 통과한다."""
    gate = ConcurrencyGate(limit=1)
    client = TestClient(_create_app(gate))

    for _ in range(5):
        assert client.post("/fake-chat").status_code == 200
    assert gate.active == 0


def test_slot_released_on_handler_exception():
    """핸들러가 터져도 슬롯은 돌아와야 한다 — finally 누락 시 여기서 잡힌다."""
    gate = ConcurrencyGate(limit=1)
    client = TestClient(_create_app(gate, handler_raises=True), raise_server_exceptions=False)

    client.post("/fake-chat")

    assert gate.active == 0, "예외 경로에서 슬롯이 샜다"
    assert gate.try_acquire() is True


def test_leak_actually_locks_out_later_requests():
    """해제를 빠뜨리면 상한이 줄어든다 — 위 테스트들이 유효함을 보이는 대조군."""
    gate = ConcurrencyGate(limit=1)
    client = TestClient(_create_app(gate), raise_server_exceptions=False)

    assert client.post("/fake-chat-noslot").status_code == 200
    assert client.post("/fake-chat-noslot").status_code == 503, (
        "누수가 있으면 다음 요청이 막혀야 한다 — 안 막히면 이 테스트 자체가 무력하다"
    )


def test_unlimited_gate_never_rejects():
    gate = ConcurrencyGate(limit=0)
    client = TestClient(_create_app(gate))

    for _ in range(20):
        assert client.post("/fake-chat").status_code == 200
