"""내부 링크 감시(LinkMonitor) + 잡 무음 실패 회귀 테스트.

운영 원칙: KMS ↔ ai-platform ↔ DocForge 는 항상 연결 — 끊김은 즉시 관측.
실사고: docforge 포화 중 kms_sync 잡이 str()이 빈 예외로 무음 소진(last_error="").
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.job_queue import QueueWorker
from src.services.link_monitor import LinkMonitor, build_link_targets


# --- build_link_targets ---


def _settings(kms="", docforge=""):
    s = MagicMock()
    s.kms_api_url = kms
    s.docforge_url = docforge
    return s


def test_build_link_targets_full():
    targets = build_link_targets(
        _settings(kms="http://kms-api:3000/api", docforge="http://docforge:5051"),
    )
    assert targets == {
        "kms": "http://kms-api:3000/api/health/live",
        "docforge": "http://docforge:5051/v1/health",
    }


def test_build_link_targets_graceful_degradation():
    """KMS 미설정 환경에서도 기동해야 한다 — 미설정 링크는 감시 제외."""
    targets = build_link_targets(_settings(docforge="http://docforge:5051"))
    assert "kms" not in targets
    assert targets["docforge"] == "http://docforge:5051/v1/health"


# --- 상태 전이 ---


def _make_monitor(results):
    """_probe 를 시퀀스 모킹한 모니터. results: [(name, up, detail) 리스트] 순차 소진."""
    monitor = LinkMonitor({"kms": "http://kms/health"}, interval_seconds=0)
    seq = iter(results)

    async def fake_probe(client, name, url):
        return next(seq)

    monitor._probe = fake_probe
    return monitor


@pytest.mark.asyncio
async def test_link_status_exposed():
    monitor = _make_monitor([("kms", True, "ok")])
    status = await monitor.check_once()
    assert status["kms"]["up"] is True
    assert status["kms"]["detail"] == "ok"
    assert status["kms"]["checked_at"] is not None


@pytest.mark.asyncio
async def test_link_down_then_recovered():
    monitor = _make_monitor([
        ("kms", True, "ok"),
        ("kms", False, "ConnectError"),
        ("kms", True, "ok"),
    ])
    await monitor.check_once()
    status = await monitor.check_once()
    assert status["kms"]["up"] is False
    assert "ConnectError" in status["kms"]["detail"]
    status = await monitor.check_once()
    assert status["kms"]["up"] is True


@pytest.mark.asyncio
async def test_start_without_targets_no_task():
    """감시 대상이 없으면(links 미설정) 주기 태스크를 만들지 않는다."""
    monitor = LinkMonitor({}, interval_seconds=60)
    await monitor.start()
    assert monitor._task is None
    await monitor.stop()


# --- 잡 무음 실패 회귀 ---


@pytest.mark.asyncio
async def test_job_failure_empty_str_exception_recorded():
    """str()이 빈 예외도 타입명이 last_error 에 남아야 한다 (무음 실패 금지)."""

    async def handler(payload):
        raise asyncio.TimeoutError()  # str() == ""

    queue = AsyncMock()
    worker = QueueWorker(queue=queue, queue_name="q", handler=handler)
    await worker._process_job({"id": "job-1", "payload": {}})

    queue.fail.assert_awaited_once()
    args = queue.fail.await_args
    error_desc = args[0][1] if len(args[0]) > 1 else args.kwargs.get("error", "")
    assert "TimeoutError" in error_desc


@pytest.mark.asyncio
async def test_job_failure_message_includes_type_and_text():
    async def handler(payload):
        raise RuntimeError("docforge unreachable")

    queue = AsyncMock()
    worker = QueueWorker(queue=queue, queue_name="q", handler=handler)
    await worker._process_job({"id": "job-2", "payload": {}})

    error_desc = queue.fail.await_args[0][1]
    assert "RuntimeError" in error_desc
    assert "docforge unreachable" in error_desc
