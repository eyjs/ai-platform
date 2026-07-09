"""Supervisor 엔트리/부트스트랩 배선 테스트 (task-002, P0-2).

`_is_supervisor_request` 순수 판별과 `/chat`·`/chat/stream`의 supervisor early-branch가
직접 모드/오케스트레이터 경로를 침범하지 않는지(§0-2) 검증한다.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.models import AgentResponse
from src.gateway.models import ChatRequest, UserContext
from src.gateway.routes import chat as chat_module
from src.gateway.routes.helpers import _is_supervisor_request


def _make_state(supervisor=None, supervisor_profile_id: str = "supervisor"):
    """`_is_supervisor_request`/supervisor 분기가 요구하는 최소 state stub."""
    settings = SimpleNamespace(
        supervisor_profile_id=supervisor_profile_id,
        default_tenant_id="default",
    )
    session_memory = AsyncMock()
    session_memory.get_turns.return_value = []
    return SimpleNamespace(
        settings=settings,
        supervisor=supervisor,
        session_memory=session_memory,
    )


def _make_user_ctx() -> UserContext:
    return UserContext(user_id="u1", user_role="VIEWER", security_level_max="PUBLIC", tenant_id=None)


# --- _is_supervisor_request 순수 판별 ---


def test_is_supervisor_request_true_when_id_matches_and_supervisor_wired():
    state = _make_state(supervisor=AsyncMock())
    assert _is_supervisor_request("supervisor", state) is True


def test_is_supervisor_request_false_for_other_chatbot_id():
    state = _make_state(supervisor=AsyncMock())
    assert _is_supervisor_request("general-chat", state) is False


def test_is_supervisor_request_true_when_chatbot_id_none():
    """Phase 3 컷오버: 자동 라우팅(None)은 supervisor가 전담한다."""
    state = _make_state(supervisor=AsyncMock())
    assert _is_supervisor_request(None, state) is True


def test_is_supervisor_request_false_when_supervisor_not_wired():
    """state.supervisor가 None(미배선)이면 안전 폴백으로 False."""
    state = _make_state(supervisor=None)
    assert _is_supervisor_request("supervisor", state) is False


def test_is_supervisor_request_uses_configured_profile_id():
    """supervisor_profile_id가 커스텀 값이어도 그 값을 기준으로 판별한다."""
    state = _make_state(supervisor=AsyncMock(), supervisor_profile_id="my-supervisor")
    assert _is_supervisor_request("my-supervisor", state) is True
    assert _is_supervisor_request("supervisor", state) is False


# --- /chat 분기: supervisor 요청 ---


@pytest.mark.asyncio
async def test_chat_supervisor_request_calls_supervise_not_prepare_chat(monkeypatch):
    """supervisor 요청 시 supervise가 호출되고 _prepare_chat/state.agent.execute는 호출되지 않는다."""
    user_ctx = _make_user_ctx()
    supervisor = AsyncMock()
    supervisor.supervise.return_value = AgentResponse(answer="종합 답변", sources=[])
    state = _make_state(supervisor=supervisor)

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=user_ctx))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    prepare_chat_mock = AsyncMock()
    monkeypatch.setattr(chat_module, "_prepare_chat", prepare_chat_mock)

    req = ChatRequest(question="보험이랑 문서 둘 다 알려줘", chatbot_id="supervisor")
    request = MagicMock()

    resp = await chat_module.chat(req, request)

    supervisor.supervise.assert_awaited_once()
    prepare_chat_mock.assert_not_awaited()
    assert resp.answer == "종합 답변"
    assert resp.response_id  # 응답 식별자는 엔트리(api)가 주입


@pytest.mark.asyncio
async def test_chat_non_supervisor_request_does_not_call_supervise(monkeypatch):
    """비-supervisor 요청은 supervise를 호출하지 않고 기존 _prepare_chat 경로로 진입한다."""
    user_ctx = _make_user_ctx()
    supervisor = AsyncMock()
    state = _make_state(supervisor=supervisor)
    state.agent = AsyncMock()
    state.session_memory.add_turn = AsyncMock()
    state.profile_store = AsyncMock()
    state.profile_store.get.return_value = None
    state.request_log_service = None
    state.response_cache_service = None

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=user_ctx))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    from src.gateway.routes.helpers import _ChatSetup
    from src.domain.agent_context import AgentContext
    from src.observability.trace_logger import RequestTrace
    from src.observability.logging import RequestContext, request_context

    real_ctx_token = request_context.set(RequestContext(
        request_id="r1", session_id="s1", profile_id="general-chat", user_id="u1",
    ))
    fake_setup = _ChatSetup(
        session_id="s1",
        plan=SimpleNamespace(mode=None),
        context=AgentContext(session_id="s1"),
        trace=RequestTrace(request_id="r1"),
        ctx_token=real_ctx_token,
        profile_id="general-chat",
    )
    prepare_chat_mock = AsyncMock(return_value=fake_setup)
    monkeypatch.setattr(chat_module, "_prepare_chat", prepare_chat_mock)
    state.agent.execute.return_value = AgentResponse(answer="직접 모드 답변", sources=[])

    req = ChatRequest(question="일반 질문", chatbot_id="general-chat")
    request = MagicMock()

    resp = await chat_module.chat(req, request)

    supervisor.supervise.assert_not_awaited()
    prepare_chat_mock.assert_awaited_once()
    assert resp.answer == "직접 모드 답변"


# --- /chat/stream 분기: supervisor 요청 ---


@pytest.mark.asyncio
async def test_chat_stream_supervisor_request_calls_supervise_not_prepare_chat_fast(monkeypatch):
    """supervisor 스트리밍 요청 시 supervise_stream이 소비되고 _prepare_chat_fast는 호출되지 않는다.

    토큰 스트리밍 계약: supervise_stream의 token 이벤트가 SSE token으로 중계되고,
    done.streamed=True면 answer 단일 재방출이 없어야 한다(이중 방출 금지).
    """
    user_ctx = _make_user_ctx()
    supervisor = AsyncMock()
    stream_called = {"count": 0}

    async def fake_stream(question, ctx, user_ctx_arg, trace=None):
        stream_called["count"] += 1
        yield {"type": "token", "data": "스트리밍 "}
        yield {"type": "token", "data": "종합 답변"}
        yield {"type": "done", "data": {
            "response": AgentResponse(answer="스트리밍 종합 답변", sources=[]),
            "streamed": True,
        }}

    supervisor.supervise_stream = fake_stream
    state = _make_state(supervisor=supervisor)

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=user_ctx))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    prepare_chat_fast_mock = AsyncMock()
    monkeypatch.setattr(chat_module, "_prepare_chat_fast", prepare_chat_fast_mock)

    req = ChatRequest(question="보험이랑 문서 둘 다 알려줘", chatbot_id="supervisor")
    request = MagicMock()

    sse_response = await chat_module.chat_stream(req, request)

    # EventSourceResponse의 제너레이터를 소진해 실제로 supervise_stream이 소비되는지 확인.
    body_iterator = sse_response.body_iterator
    events = [event async for event in body_iterator]

    assert stream_called["count"] == 1
    prepare_chat_fast_mock.assert_not_awaited()
    token_events = [e for e in events if e.get("event") == "token"]
    assert len(token_events) == 2  # streamed=True → answer 단일 재방출 없음
    assert any(e.get("event") == "done" for e in events)
