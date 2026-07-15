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
