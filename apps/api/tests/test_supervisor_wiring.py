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


# --- supervisor 경로 관측성: request_log 기록 + 초기 진행 이벤트 ---


def _capture_enqueue(monkeypatch):
    """chat 모듈의 safe_enqueue를 가로채 RequestLogEntry를 수집한다."""
    captured: list = []
    monkeypatch.setattr(
        chat_module, "safe_enqueue", lambda svc, entry: captured.append(entry),
    )
    return captured


@pytest.mark.asyncio
async def test_chat_stream_supervisor_emits_initial_trace_and_request_log(monkeypatch):
    """스트리밍 supervisor 경로: 첫 이벤트는 즉시 진행 trace, 종료 시 request_log 기록."""
    import json as json_lib

    user_ctx = _make_user_ctx()
    supervisor = AsyncMock()

    async def fake_stream(question, ctx, user_ctx_arg, trace=None):
        yield {"type": "trace", "data": {"step": "tool_execution", "status": "start"}}
        yield {"type": "token", "data": "답변"}
        yield {"type": "done", "data": {
            "response": AgentResponse(answer="답변", sources=[]),
            "streamed": True,
        }}

    supervisor.supervise_stream = fake_stream
    state = _make_state(supervisor=supervisor)
    state.request_log_service = MagicMock()
    captured = _capture_enqueue(monkeypatch)

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=user_ctx))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    req = ChatRequest(question="가입 나이 알려줘", chatbot_id=None)
    request = MagicMock()
    request.client = None

    sse_response = await chat_module.chat_stream(req, request)
    events = [event async for event in sse_response.body_iterator]

    # 첫 이벤트 = 연결 생존 신호 (첫 토큰까지 무신호 구간 제거)
    assert events[0]["event"] == "trace"
    first = json_lib.loads(events[0]["data"])
    assert first == {"step": "supervisor", "status": "start"}

    # 위임 서브의 진행 trace도 SSE로 중계된다
    trace_events = [json_lib.loads(e["data"]) for e in events if e["event"] == "trace"]
    assert {"step": "tool_execution", "status": "start"} in trace_events

    # request_log 기록 (기존 관측 공백 해소)
    assert len(captured) == 1
    entry = captured[0]
    assert entry.profile_id == "supervisor"
    assert entry.status_code == 200
    assert entry.request_preview == "가입 나이 알려줘"
    assert entry.response_preview == "답변"
    assert entry.response_id
    assert entry.user_id == "u1"
    # 레이어별 처리시간 — 레거시 경로와 동일하게 trace 요약을 영속화
    assert entry.latency_breakdown is not None
    assert "request_id" in entry.latency_breakdown


@pytest.mark.asyncio
async def test_chat_supervisor_nonstream_enqueues_request_log(monkeypatch):
    """비스트리밍 supervisor 경로도 request_log에 남는다."""
    user_ctx = _make_user_ctx()
    supervisor = AsyncMock()
    supervisor.supervise.return_value = AgentResponse(answer="종합 답변", sources=[])
    state = _make_state(supervisor=supervisor)
    state.request_log_service = MagicMock()
    captured = _capture_enqueue(monkeypatch)

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=user_ctx))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    req = ChatRequest(question="보험 알려줘", chatbot_id="supervisor")
    request = MagicMock()
    request.client = None

    resp = await chat_module.chat(req, request)

    assert resp.answer == "종합 답변"
    assert len(captured) == 1
    entry = captured[0]
    assert entry.profile_id == "supervisor"
    assert entry.status_code == 200
    assert entry.response_preview == "종합 답변"
    assert entry.response_id == resp.response_id
    assert entry.latency_breakdown is not None
    # supervise에 trace가 전달돼 위임 서브 실행이 레이어 타이밍을 기록할 수 있다
    assert supervisor.supervise.await_args.kwargs.get("trace") is not None


@pytest.mark.asyncio
async def test_chat_supervisor_nonstream_logs_500_on_error(monkeypatch):
    """supervise 예외 시에도 request_log에 500으로 남는다 (무음 실패 금지)."""
    user_ctx = _make_user_ctx()
    supervisor = AsyncMock()
    supervisor.supervise.side_effect = RuntimeError("boom")
    state = _make_state(supervisor=supervisor)
    state.request_log_service = MagicMock()
    captured = _capture_enqueue(monkeypatch)

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=user_ctx))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    req = ChatRequest(question="보험 알려줘", chatbot_id="supervisor")
    request = MagicMock()
    request.client = None

    with pytest.raises(RuntimeError):
        await chat_module.chat(req, request)

    assert len(captured) == 1
    assert captured[0].status_code == 500
    assert captured[0].error_code == "supervisor_error"


@pytest.mark.asyncio
async def test_chat_nonstream_records_nonzero_latency(monkeypatch):
    """비스트리밍 /chat의 latency_ms가 0으로 고정되지 않는다 (latency_timer 순서 버그 회귀).

    기존에는 latency_timer의 elapsed_ms가 컨텍스트 종료 시점에 계산되는데
    로그 enqueue가 안쪽 finally라 항상 초기값 0을 읽었다.
    """
    user_ctx = _make_user_ctx()
    state = _make_state(supervisor=None)  # supervisor 미배선 → 레거시 직접 경로
    state.agent = AsyncMock()
    state.session_memory.add_turn = AsyncMock()
    state.profile_store = AsyncMock()
    state.profile_store.get.return_value = None
    state.request_log_service = MagicMock()
    state.response_cache_service = None
    captured = _capture_enqueue(monkeypatch)

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=user_ctx))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    # 가짜 시계: 호출마다 0.1초 전진 → elapsed가 반드시 양수
    fake_now = {"t": 100.0}

    def fake_monotonic():
        fake_now["t"] += 0.1
        return fake_now["t"]

    monkeypatch.setattr(chat_module.time, "monotonic", fake_monotonic)

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
    monkeypatch.setattr(chat_module, "_prepare_chat", AsyncMock(return_value=fake_setup))
    state.agent.execute.return_value = AgentResponse(answer="답", sources=[])

    req = ChatRequest(question="일반 질문", chatbot_id="general-chat")
    request = MagicMock()
    request.client = None

    await chat_module.chat(req, request)

    assert len(captured) == 1
    assert captured[0].latency_ms > 0


@pytest.mark.asyncio
async def test_supervisor_faithfulness_score_persisted(monkeypatch):
    """서브 가드레일 점수가 request_log에 영속되고 클라이언트 응답에선 제거된다."""
    user_ctx = _make_user_ctx()
    supervisor = AsyncMock()
    supervisor.supervise.return_value = AgentResponse(
        answer="답", sources=[], guardrail_score=0.87,
    )
    state = _make_state(supervisor=supervisor)
    state.request_log_service = MagicMock()
    captured = _capture_enqueue(monkeypatch)

    monkeypatch.setattr(chat_module, "_get_app_state", lambda request: state)
    monkeypatch.setattr(chat_module, "_authenticate", AsyncMock(return_value=user_ctx))
    monkeypatch.setattr(chat_module, "_check_rate_limit", AsyncMock(return_value=None))

    req = ChatRequest(question="q", chatbot_id="supervisor")
    request = MagicMock()
    request.client = None

    resp = await chat_module.chat(req, request)

    assert captured[0].faithfulness_score == 0.87  # 로그 영속
    assert resp.guardrail_score is None  # 내부 필드는 응답에서 제거 (레거시 계약)
