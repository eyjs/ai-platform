"""FailoverLLMProvider — DGX primary 실패 시 현행 로컬 폴백 계약."""

import httpx
import pytest
from unittest.mock import AsyncMock

from src.infrastructure.providers.base import LLMProvider, StreamChunk
from src.infrastructure.providers.llm.failover import FailoverLLMProvider


class _Fake(LLMProvider):
    def __init__(self, name, fail_with=None, answer="답"):
        self.name, self._fail, self._answer = name, fail_with, answer
        self.calls = 0

    async def generate(self, prompt, system="", **kwargs):
        self.calls += 1
        if self._fail:
            raise self._fail
        return f"{self.name}:{self._answer}"

    async def generate_json(self, prompt, system="", **kwargs):
        self.calls += 1
        if self._fail:
            raise self._fail
        return {"from": self.name}

    async def generate_stream_typed(self, prompt, system="", **kwargs):
        self.calls += 1
        if self._fail:
            raise self._fail
        yield StreamChunk(kind="answer", content=f"{self.name}:")
        yield StreamChunk(kind="answer", content=self._answer)


@pytest.mark.asyncio
async def test_primary_healthy_no_fallback():
    p, f = _Fake("dgx"), _Fake("local")
    prov = FailoverLLMProvider(p, f)
    assert await prov.generate("q") == "dgx:답"
    assert f.calls == 0


@pytest.mark.asyncio
async def test_connection_error_falls_back_and_cooldown_skips_primary():
    p = _Fake("dgx", fail_with=httpx.ConnectError("down"))
    f = _Fake("local")
    prov = FailoverLLMProvider(p, f, cooldown_seconds=60)
    assert await prov.generate("q") == "local:답"   # 폴백
    assert await prov.generate("q") == "local:답"   # 쿨다운 중 primary 스킵
    assert p.calls == 1 and f.calls == 2


@pytest.mark.asyncio
async def test_cooldown_expiry_retries_primary():
    p = _Fake("dgx", fail_with=httpx.ConnectError("down"))
    f = _Fake("local")
    prov = FailoverLLMProvider(p, f, cooldown_seconds=0.0)  # 즉시 재시도 허용
    await prov.generate("q")
    p._fail = None  # DGX 복구
    assert await prov.generate("q") == "dgx:답"  # 자동 복귀


@pytest.mark.asyncio
async def test_recovery_is_logged_once(caplog):
    """복구 로그가 없으면 저품질(폴백 모델) 구간의 끝을 특정할 수 없다.

    전이일 때만 남긴다 — 이후 정상 호출은 무소음이어야 한다.
    """
    p = _Fake("dgx", fail_with=httpx.ConnectError("down"))
    f = _Fake("local")
    prov = FailoverLLMProvider(p, f, cooldown_seconds=0.0, label="main")

    await prov.generate("q")          # 폴백 발동
    p._fail = None
    with caplog.at_level("INFO"):
        await prov.generate("q")      # 복귀 — 로그 1회
        await prov.generate("q")      # 이후 정상 — 무소음
    recovered = [r for r in caplog.records if "llm_failover_recovered" in r.getMessage()]
    assert len(recovered) == 1


@pytest.mark.asyncio
async def test_healthy_primary_never_logs_recovery(caplog):
    """폴백을 탄 적 없으면 복구 로그도 없다(가짜 전이 금지)."""
    prov = FailoverLLMProvider(_Fake("dgx"), _Fake("local"))
    with caplog.at_level("INFO"):
        await prov.generate("q")
    assert not [r for r in caplog.records if "llm_failover_recovered" in r.getMessage()]


@pytest.mark.asyncio
async def test_non_availability_error_propagates():
    """논리 오류(4xx 등)는 폴백해도 같은 결과 — 전파한다."""
    p = _Fake("dgx", fail_with=ValueError("bad prompt"))
    f = _Fake("local")
    prov = FailoverLLMProvider(p, f)
    with pytest.raises(ValueError):
        await prov.generate("q")
    assert f.calls == 0


@pytest.mark.asyncio
async def test_stream_fails_over_before_first_chunk():
    p = _Fake("dgx", fail_with=httpx.ConnectTimeout("t/o"))
    f = _Fake("local")
    prov = FailoverLLMProvider(p, f)
    chunks = [c.content async for c in prov.generate_stream_typed("q")]
    assert "".join(chunks) == "local:답"


class _MidStreamFail(LLMProvider):
    async def generate(self, prompt, system="", **kwargs): ...
    async def generate_json(self, prompt, system="", **kwargs): ...

    async def generate_stream_typed(self, prompt, system="", **kwargs):
        yield StreamChunk(kind="answer", content="부분")
        raise httpx.ReadError("mid-stream")


@pytest.mark.asyncio
async def test_stream_after_first_chunk_propagates():
    """부분 출력 후 실패는 폴백하지 않는다(중복 노출 방지) — 전파."""
    prov = FailoverLLMProvider(_MidStreamFail(), _Fake("local"))
    with pytest.raises(httpx.ReadError):
        async for _ in prov.generate_stream_typed("q"):
            pass


def test_ollama_dgx_timeout_axes():
    """DGX primary: connect은 짧게(다운 즉시 폴백), read는 무제한(긴 생성 허용).

    - connect이 길면 원격 오프라인 시 매 재프로브가 stall → 이중화 무의미.
    - read가 짧으면 수 분~수십 분 걸리는 복잡한 생성이 중간에 잘린다.
    """
    from src.infrastructure.providers.llm.ollama import OllamaProvider

    prov = OllamaProvider(
        base_url="http://remote:11434", connect_timeout=3.0, read_timeout=None,
    )
    assert prov._client.timeout.connect == 3.0
    assert prov._client.timeout.read is None
