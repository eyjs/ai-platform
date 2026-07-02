"""HTTP provider 회복력 유틸(재시도 + 서킷 브레이커) 테스트.

임베딩처럼 degrade 불가능한 필수 HTTP 의존점의 견고화(옵션 나)를 검증한다.
"""

import httpx
import pytest

from src.infrastructure.providers._resilience import (
    CircuitBreaker,
    CircuitOpenError,
    is_transient,
    retry_async,
)


def _http_500() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://x/embed")
    resp = httpx.Response(500, request=req)
    return httpx.HTTPStatusError("500", request=req, response=resp)


def _http_400() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://x/embed")
    resp = httpx.Response(400, request=req)
    return httpx.HTTPStatusError("400", request=req, response=resp)


def test_is_transient_classification():
    assert is_transient(httpx.ConnectError("refused")) is True
    assert is_transient(httpx.ReadTimeout("timeout")) is True
    assert is_transient(_http_500()) is True
    assert is_transient(_http_400()) is False
    assert is_transient(ValueError("bad shape")) is False


@pytest.mark.asyncio
async def test_retry_recovers_transient_blip():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("blip")
        return "ok"

    out = await retry_async(flaky, attempts=3, base_delay=0.0)
    assert out == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_non_transient_not_retried():
    calls = {"n": 0}

    async def bad_request():
        calls["n"] += 1
        raise _http_400()

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(bad_request, attempts=3, base_delay=0.0)
    assert calls["n"] == 1  # 4xx 는 재시도 안 함


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold_and_fast_fails():
    breaker = CircuitBreaker(fail_threshold=2, cooldown_seconds=60.0, name="t")

    async def always_down():
        raise httpx.ConnectError("down")

    # 실패 1: 아직 임계치 미만
    with pytest.raises(httpx.ConnectError):
        await retry_async(always_down, attempts=1, base_delay=0.0, breaker=breaker)
    assert breaker.is_open is False
    # 실패 2: 임계치 도달 → 서킷 개방
    with pytest.raises(httpx.ConnectError):
        await retry_async(always_down, attempts=1, base_delay=0.0, breaker=breaker)
    assert breaker.is_open is True

    # 개방 상태: fn 호출 없이 즉시 fast-fail
    called = {"n": 0}

    async def probe():
        called["n"] += 1
        return "ok"

    with pytest.raises(CircuitOpenError):
        await retry_async(probe, attempts=3, base_delay=0.0, breaker=breaker)
    assert called["n"] == 0  # fast-fail — 서버를 때리지 않음


@pytest.mark.asyncio
async def test_circuit_half_open_recovers_on_success():
    # cooldown=0 → 즉시 half-open. 성공하면 닫힘.
    breaker = CircuitBreaker(fail_threshold=1, cooldown_seconds=0.0, name="t")

    async def down():
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        await retry_async(down, attempts=1, base_delay=0.0, breaker=breaker)
    # cooldown 0 이므로 즉시 half-open 허용
    assert breaker.is_open is False

    out = await retry_async(lambda: _ok(), attempts=1, base_delay=0.0, breaker=breaker)
    assert out == "ok"
    assert breaker.is_open is False


async def _ok():
    return "ok"


@pytest.mark.asyncio
async def test_embedding_provider_retries_then_succeeds(monkeypatch):
    """HttpEmbeddingProvider 가 일시적 blip 을 재시도로 흡수하는지."""
    from src.infrastructure.providers.embedding.http_embedding import HttpEmbeddingProvider

    provider = HttpEmbeddingProvider("http://mlx:8103", retry_attempts=3)

    state = {"n": 0}

    async def fake_post(url, json):
        state["n"] += 1
        if state["n"] < 2:
            raise httpx.ConnectError("blip")

        class _R:
            def raise_for_status(self):
                return None

            def json(self):
                return {"embeddings": [[0.1, 0.2, 0.3] for _ in json["inputs"]]}

        return _R()

    monkeypatch.setattr(provider._client, "post", fake_post)

    out = await provider.embed_batch(["a", "b"])
    assert len(out) == 2
    assert state["n"] == 2  # 1회 실패 후 재시도 성공


@pytest.mark.asyncio
async def test_reranker_retries_then_succeeds(monkeypatch):
    from src.infrastructure.providers.reranking.http_reranker import HttpRerankerProvider

    r = HttpRerankerProvider("http://mlx:8102", retry_attempts=3)
    state = {"n": 0}

    async def flaky_http(query, documents, top_k):
        state["n"] += 1
        if state["n"] < 2:
            raise httpx.ConnectError("blip")
        return [{"index": 0, "score": 0.9}]

    monkeypatch.setattr(r, "_http_rerank", flaky_http)
    out = await r.rerank("q", ["a", "b", "c"], top_k=1)
    assert out == [{"index": 0, "score": 0.9}]
    assert state["n"] == 2  # 재시도로 복구, degrade 안 함


@pytest.mark.asyncio
async def test_llm_stream_retries_before_first_chunk(monkeypatch):
    from src.infrastructure.providers.llm.http_llm import HttpLLMProvider
    from src.infrastructure.providers.base import StreamChunk

    p = HttpLLMProvider("http://mlx:8106", retry_attempts=3)
    state = {"n": 0}

    async def fake_stream_once(system_msg, prompt):
        state["n"] += 1
        if state["n"] < 2:
            raise httpx.ConnectError("blip")
            yield  # noqa — async generator 로 만들기 위한 unreachable yield
        yield StreamChunk(kind="answer", content="hello")

    monkeypatch.setattr(p, "_stream_once", fake_stream_once)
    out = [c.content async for c in p.generate_stream_typed("q")]
    assert out == ["hello"]
    assert state["n"] == 2  # 첫 청크 이전 blip → 재시도


@pytest.mark.asyncio
async def test_llm_stream_no_retry_after_first_chunk(monkeypatch):
    """첫 청크 방출 후 실패는 재시도하지 않는다(중복 방출 방지)."""
    from src.infrastructure.providers.llm.http_llm import HttpLLMProvider
    from src.infrastructure.providers.base import StreamChunk

    p = HttpLLMProvider("http://mlx:8106", retry_attempts=3)
    state = {"n": 0}

    async def fake_stream_once(system_msg, prompt):
        state["n"] += 1
        yield StreamChunk(kind="answer", content="partial")
        raise httpx.ConnectError("mid-stream")

    monkeypatch.setattr(p, "_stream_once", fake_stream_once)
    collected = []
    with pytest.raises(httpx.ConnectError):
        async for c in p.generate_stream_typed("q"):
            collected.append(c.content)
    assert collected == ["partial"]  # 중복 방출 없음
    assert state["n"] == 1  # 재시도 안 함


@pytest.mark.asyncio
async def test_llm_stream_fast_fails_when_circuit_open(monkeypatch):
    from src.infrastructure.providers.llm.http_llm import HttpLLMProvider

    import time as _t

    # cooldown(기본 15s) 내에서 열린 상태 유지되도록 opened_at 을 현재로 설정
    p = HttpLLMProvider("http://mlx:8106")
    p._breaker._opened_at = _t.monotonic()
    assert p._breaker.is_open is True

    called = {"n": 0}

    async def fake_stream_once(system_msg, prompt):
        called["n"] += 1
        yield  # pragma: no cover

    monkeypatch.setattr(p, "_stream_once", fake_stream_once)
    with pytest.raises(CircuitOpenError):
        async for _ in p.generate_stream_typed("q"):
            pass
    assert called["n"] == 0  # fast-fail, 스트림 시작 안 함
