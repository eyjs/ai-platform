"""구조화 로깅 시스템 테스트."""

import json
import logging
import sys

from src.common.exceptions import InfraError, RouterAIError
from src.observability.logging import (
    HumanReadableFormatter,
    RequestContext,
    StructuredFormatter,
    StructuredLogger,
    get_logger,
    request_context,
)
from src.observability.trace_logger import RequestTrace


def test_structured_formatter_basic():
    formatter = StructuredFormatter()
    record = logging.LogRecord(
        name="test.module", level=logging.INFO, pathname="",
        lineno=0, msg="test_event", args=(), exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.module"
    assert parsed["msg"] == "test_event"


def test_structured_formatter_with_context():
    formatter = StructuredFormatter()
    ctx = RequestContext(request_id="req-123", profile_id="insurance-qa")
    token = request_context.set(ctx)
    try:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="",
            lineno=0, msg="event", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["request_id"] == "req-123"
        assert parsed["profile_id"] == "insurance-qa"
    finally:
        request_context.reset(token)


def test_structured_formatter_with_extra_data():
    formatter = StructuredFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="",
        lineno=0, msg="event", args=(), exc_info=None,
    )
    record._structured_data = {"latency_ms": 45.2, "chunks": 5}
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["latency_ms"] == 45.2
    assert parsed["chunks"] == 5


def test_human_readable_formatter():
    formatter = HumanReadableFormatter()
    ctx = RequestContext(request_id="abcdefgh-1234")
    token = request_context.set(ctx)
    try:
        record = logging.LogRecord(
            name="src.router.ai_router", level=logging.INFO, pathname="",
            lineno=0, msg="route_complete", args=(), exc_info=None,
        )
        record._structured_data = {"question_type": "STANDALONE"}
        output = formatter.format(record)
        assert "abcdefgh" in output
        assert "router.ai_router" in output
        assert "question_type=STANDALONE" in output
    finally:
        request_context.reset(token)


def test_get_logger():
    logger = get_logger("test.module")
    assert isinstance(logger, StructuredLogger)


def test_request_context_elapsed():
    ctx = RequestContext(request_id="test")
    assert ctx.elapsed_ms >= 0


def test_request_trace():
    trace = RequestTrace(request_id="req-001")
    node = trace.start_node("router")
    node.finish(question_type="STANDALONE")
    trace.add_node("tool:rag_search", 150.0, chunks=5)

    summary = trace.summary()
    assert summary["request_id"] == "req-001"
    assert len(summary["nodes"]) == 2
    assert summary["nodes"][0]["node"] == "router"
    assert summary["nodes"][1]["node"] == "tool:rag_search"
    assert summary["nodes"][1]["chunks"] == 5


def test_request_trace_total_ms():
    trace = RequestTrace(request_id="req-002")
    assert trace.total_ms >= 0


# --- Layer-Aware 로깅 ---


def _make_record(msg, structured_data=None, exc_info=None, name="src.router.ai_router"):
    record = logging.LogRecord(
        name=name, level=logging.ERROR, pathname="",
        lineno=0, msg=msg, args=(), exc_info=exc_info,
    )
    record._structured_data = structured_data or {}
    return record


def test_json_formatter_layer_field():
    """JSON 포맷터에 layer/component가 포함된다."""
    fmt = StructuredFormatter()
    record = _make_record("L0_context_resolve", {
        "layer": "ROUTER",
        "component": "ContextResolver",
        "latency_ms": 12.3,
    })
    output = json.loads(fmt.format(record))
    assert output["layer"] == "ROUTER"
    assert output["component"] == "ContextResolver"
    assert output["latency_ms"] == 12.3


def test_json_formatter_app_error_auto_extract():
    """AppError 예외의 layer/error_code가 자동 추출된다."""
    fmt = StructuredFormatter()
    try:
        raise RouterAIError("LLM JSON 파싱 실패", component="ContextResolver")
    except Exception:
        record = _make_record("L0_fallback", exc_info=sys.exc_info())

    output = json.loads(fmt.format(record))
    assert output["layer"] == "ROUTER"
    assert output["error_code"] == "ERR_ROUTER_001"
    assert output["component"] == "ContextResolver"
    assert output["error_type"] == "RouterAIError"


def test_json_formatter_app_error_no_override():
    """명시적 layer가 AppError의 layer보다 우선한다."""
    fmt = StructuredFormatter()
    try:
        raise RouterAIError("test")
    except Exception:
        record = _make_record("test", {"layer": "CUSTOM"}, exc_info=sys.exc_info())

    output = json.loads(fmt.format(record))
    assert output["layer"] == "CUSTOM"


def test_json_formatter_non_app_error():
    """일반 Exception은 layer 없이 에러 정보만 출력."""
    fmt = StructuredFormatter()
    try:
        raise ConnectionError("DB 연결 실패")
    except Exception:
        record = _make_record("db_error", exc_info=sys.exc_info())

    output = json.loads(fmt.format(record))
    assert "layer" not in output
    assert output["error_type"] == "ConnectionError"


def test_human_formatter_with_layer():
    """Human 포맷터에서 [LAYER:Component] 형태로 출력."""
    fmt = HumanReadableFormatter()
    token = request_context.set(RequestContext(request_id="test-1234"))
    try:
        record = _make_record("L0_fallback", {
            "layer": "ROUTER",
            "component": "ContextResolver",
            "error": "LLM 파싱 실패",
        })
        output = fmt.format(record)
        assert "[ROUTER:ContextResolver]" in output
        assert "L0_fallback" in output
    finally:
        request_context.reset(token)


def test_human_formatter_layer_only():
    """component 없이 layer만 있으면 [LAYER] 형태."""
    fmt = HumanReadableFormatter()
    token = request_context.set(RequestContext())
    try:
        record = _make_record("route_complete", {"layer": "ROUTER"})
        output = fmt.format(record)
        assert "[ROUTER]" in output
    finally:
        request_context.reset(token)
