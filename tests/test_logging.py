"""구조화 로깅 시스템 테스트."""

import json
import logging

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
