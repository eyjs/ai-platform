"""response_id 전파 + faithfulness 스코어 수집 단위 테스트.

S2: AgentResponse.response_id (/chat), SSE done 이벤트 response_id (/chat/stream)
S3: guardrail results → request_log 의 faithfulness_score 파라미터

외부 의존성이 많아 실제 엔드포인트를 호출하지 않고,
- AgentResponse.response_id 필드 존재
- RequestLogEntry.faithfulness_score / response_id 필드 존재
- _extract_faithfulness_score 헬퍼 동작
- run_guardrail_chain 이 results 에 score 포함
- FaithfulnessGuard.check 가 GuardrailResult.score 세팅
정도를 빠르게 검증한다.
"""

from __future__ import annotations

import pytest

from src.agent.graph_executor import _extract_faithfulness_score
from src.agent.nodes import run_guardrail_chain
from src.domain.models import AgentResponse
from src.observability.request_log_models import RequestLogEntry
from src.safety.base import GuardrailContext, GuardrailResult


class _FakeGuard:
    name = "faithfulness"

    def __init__(self, score):
        self._score = score

    async def check(self, answer, context):
        return GuardrailResult.passed(score=self._score)


def test_agent_response_has_response_id_field():
    """S2: AgentResponse 가 response_id 필드를 갖는다."""
    resp = AgentResponse(answer="hi", response_id="abc-123")
    assert resp.response_id == "abc-123"


def test_agent_response_default_response_id_is_none():
    resp = AgentResponse(answer="hi")
    assert resp.response_id is None


def test_request_log_entry_has_response_id_and_faithfulness_score():
    """S3: RequestLogEntry 에 두 필드가 존재."""
    entry = RequestLogEntry(
        status_code=200,
        latency_ms=1,
        response_id="r1",
        faithfulness_score=0.7,
    )
    assert entry.response_id == "r1"
    assert entry.faithfulness_score == 0.7


def test_extract_faithfulness_score_returns_float():
    results = {"faithfulness": {"action": "pass", "ms": 1.0, "score": 0.7}}
    assert _extract_faithfulness_score(results) == 0.7


def test_extract_faithfulness_score_none_when_missing():
    assert _extract_faithfulness_score({}) is None
    assert _extract_faithfulness_score({"faithfulness": {"action": "pass"}}) is None
    assert _extract_faithfulness_score({"other": {"score": 0.5}}) is None


@pytest.mark.asyncio
async def test_run_guardrail_chain_exposes_score_in_results():
    """nodes.run_guardrail_chain 이 results dict 에 score 를 포함."""
    guards = {"faithfulness": _FakeGuard(score=0.9)}
    ctx = GuardrailContext(question="q", source_documents=[{"content": "..."}])
    _, results = await run_guardrail_chain("answer", ["faithfulness"], guards, ctx)

    assert "faithfulness" in results
    assert results["faithfulness"]["score"] == 0.9


@pytest.mark.asyncio
async def test_faithfulness_guard_returns_none_when_no_sources():
    """source_documents 없으면 score=None (측정 불가)."""
    from src.safety.faithfulness import FaithfulnessGuard

    guard = FaithfulnessGuard(router_llm=None)
    ctx = GuardrailContext(question="q", source_documents=[])
    result = await guard.check("answer", ctx)
    assert result.action == "pass"
    assert result.score is None


@pytest.mark.asyncio
async def test_faithfulness_guard_returns_1_0_when_clean():
    """소스에 답변 숫자가 모두 존재 → score=1.0."""
    from src.safety.faithfulness import FaithfulnessGuard

    guard = FaithfulnessGuard(router_llm=None)
    ctx = GuardrailContext(
        question="?",
        source_documents=[{"content": "순수 텍스트", "file_name": "doc.pdf"}],
        response_policy="balanced",
    )
    result = await guard.check("순수 텍스트", ctx)
    assert result.action == "pass"
    assert result.score == 1.0
