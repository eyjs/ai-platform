"""내부 링크 감시(LinkMonitor) + 잡 무음 실패 회귀 테스트.

운영 원칙: KMS ↔ ai-platform ↔ DocForge 는 항상 연결 — 끊김은 즉시 관측.
실사고: docforge 포화 중 kms_sync 잡이 str()이 빈 예외로 무음 소진(last_error="").
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.job_queue import QueueWorker
from src.services.link_monitor import (
    GenerateProbe,
    LinkMonitor,
    build_link_targets,
    build_llm_probe_targets,
)


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


# --- LLM 서빙 생성 프로브 ---
#
# 실사고(2026-07-16): 8104가 7일간 /health 200을 주면서 생성은 전건 500이었다.
# GET 프로브로는 못 잡는다 — 아래는 그 회귀를 고정한다.


def _llm_settings(**kw):
    s = MagicMock()
    s.dgx_llm_url = kw.get("dgx", "")
    s.dgx_main_model = kw.get("dgx_model", "qwen3.6:35b-a3b")
    s.dgx_local_fallback = kw.get("fallback", False)
    s.main_llm_server_url = kw.get("main", "")
    s.router_llm_server_url = kw.get("router", "")
    s.report_llm_server_url = kw.get("report", "")
    s.fortune_llm_server_url = kw.get("fortune", "")
    s.orchestrator_server_url = kw.get("orchestrator", "")
    return s


def test_llm_probe_targets_dgx_with_fallback():
    """폴백이 켜지면 DGX와 로컬 MLX를 모두 감시한다."""
    targets = build_llm_probe_targets(_llm_settings(
        dgx="http://dgx:11434", fallback=True,
        main="http://mlx:8106", report="http://mlx:8104",
    ))
    assert targets["llm:dgx"] == GenerateProbe(url="http://dgx:11434", model="qwen3.6:35b-a3b")
    assert targets["llm:local:main"].url == "http://mlx:8106"
    assert targets["llm:local:report"].url == "http://mlx:8104"


def test_llm_probe_targets_skips_unwired_local():
    """DGX 단독(폴백 off)이면 로컬 MLX는 배선되지 않으므로 감시하지 않는다.

    쓰지도 않는 서버의 장애로 시끄러워지면 감시 목록 자체가 죽은 설정이 된다.
    """
    targets = build_llm_probe_targets(_llm_settings(
        dgx="http://dgx:11434", fallback=False, main="http://mlx:8106",
    ))
    assert set(targets) == {"llm:dgx"}


def test_llm_probe_targets_dedupes_shared_url():
    """main·fortune이 같은 서버(8106)를 공유하면 한 번만 찌른다."""
    targets = build_llm_probe_targets(_llm_settings(
        main="http://mlx:8106", fortune="http://mlx:8106",
    ))
    assert list(targets) == ["llm:local:main"]


@pytest.mark.asyncio
async def test_generate_probe_catches_500_that_health_would_miss():
    """8104 회귀: 생성이 500이면 down. health(GET)만 봤다면 up으로 오독했다."""
    monitor = LinkMonitor({"llm:x": GenerateProbe(url="http://mlx:8104")}, interval_seconds=0)
    client = MagicMock()
    client.post = AsyncMock(return_value=MagicMock(
        status_code=500, text='{"detail":"Generation failed: unicode-escape"}',
    ))
    name, up, detail = await monitor._probe(client, "llm:x", GenerateProbe(url="http://mlx:8104"))
    assert up is False
    assert "unicode-escape" in detail


@pytest.mark.asyncio
async def test_generate_probe_rejects_200_with_bad_shape():
    """200이어도 choices가 없으면 서빙 불가로 본다."""
    resp = MagicMock(status_code=200)
    resp.json = MagicMock(return_value={"error": "no model"})
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    monitor = LinkMonitor({}, interval_seconds=0)
    _, up, detail = await monitor._probe(client, "llm:x", GenerateProbe(url="http://mlx:8104"))
    assert up is False
    assert "형식 불량" in detail


@pytest.mark.asyncio
async def test_generate_probe_ok_even_with_empty_content():
    """thinking 모델은 1토큰 상한에서 content가 빌 수 있다 — 고장이 아니다."""
    resp = MagicMock(status_code=200)
    resp.json = MagicMock(return_value={"choices": [{"message": {"content": ""}}]})
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    monitor = LinkMonitor({}, interval_seconds=0)
    _, up, detail = await monitor._probe(client, "llm:x", GenerateProbe(url="http://mlx:8104"))
    assert up is True
    assert detail == "generate ok"


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
