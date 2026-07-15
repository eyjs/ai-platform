"""연산 왜곡 가드 — max→합산 재서술의 결정론 검출 + 스트림 재생성."""

import pytest
from unittest.mock import AsyncMock

from src.safety.base import GuardrailContext
from src.safety.faithfulness import FaithfulnessGuard

SOURCE = {
    "content": "본인이 실제로 부담한 금액에서 10만원과 보상대상의료비의 30% 중 큰 금액을 뺀 금액을 보상합니다.",
    "file_name": "약관.pdf",
}


def _ctx():
    return GuardrailContext(
        question="자부담금 얼마야",
        source_documents=[SOURCE],
        profile_id="s1",
        response_policy="balanced",
    )


class TestOperatorDistortion:
    @pytest.mark.asyncio
    async def test_sum_restatement_flagged(self):
        """원문 max를 답변이 합산으로 재서술 → score 0.2 심각 warn."""
        guard = FaithfulnessGuard()
        answer = "공제금액은 10만원과 보상대상의료비의 30%를 합산한 금액입니다."
        result = await guard.check(answer, _ctx())
        assert result.action == "warn"
        assert result.score == 0.2
        assert "합산" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_faithful_max_restatement_passes(self):
        """원문 연산을 보존("중 큰 금액")하면 통과."""
        guard = FaithfulnessGuard()
        answer = "공제금액은 10만원과 보상대상의료비의 30% 중 큰 금액입니다."
        result = await guard.check(answer, _ctx())
        assert result.score != 0.2

    @pytest.mark.asyncio
    async def test_unrelated_sum_not_flagged(self):
        """소스 max 쌍과 무관한 합산 표현은 오탐하지 않는다."""
        guard = FaithfulnessGuard()
        answer = "보장 항목은 입원비와 수술비입니다."
        result = await guard.check(answer, _ctx())
        assert result.score != 0.2


class _FakeGuard:
    """1차: 심각 위반(0.2) → 2차: 통과(1.0)."""

    name = "faithfulness"

    def __init__(self):
        self.calls = 0

    async def check(self, answer, context):
        from src.safety.base import GuardrailResult
        self.calls += 1
        if self.calls == 1:
            return GuardrailResult.warn("계산 왜곡", None, score=0.2)
        return GuardrailResult.passed(score=1.0)


class _TwoAnswerLLM:
    def __init__(self):
        self.calls = 0

    async def generate_stream_typed(self, prompt, system="", cacheable_system="",
                                    volatile_system="", max_tokens=None):
        from src.infrastructure.providers.base import StreamChunk
        self.calls += 1
        answer = "합산 기준 답변" if self.calls == 1 else "중 큰 금액 기준 답변"
        # 재생성 프롬프트엔 가드 피드백이 volatile로 주입돼야 한다
        if self.calls == 2:
            assert "품질 검증" in volatile_system
        yield StreamChunk(kind="answer", content=answer)


@pytest.mark.asyncio
async def test_stream_regenerates_on_guardrail_verdict():
    """재생성 결정권 = 가드레일 판정(score<0.35). 같은 컨텍스트로 1회 재생성."""
    from unittest.mock import MagicMock
    from src.agent.graph_executor import GraphExecutor
    from src.domain.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType, ToolCall
    from src.domain.models import AgentMode, SearchScope
    from src.tools.base import ToolResult

    llm = _TwoAnswerLLM()
    guard = _FakeGuard()
    registry = AsyncMock()
    registry.get = MagicMock(return_value=None)
    registry.execute = AsyncMock(return_value=ToolResult(
        success=True,
        data=[{"document_id": "d1", "title": "약관", "content": "10만원과 30% 중 큰 금액", "score": 0.9}],
    ))
    executor = GraphExecutor(main_llm=llm, tool_registry=registry, guardrails={"faithfulness": guard})

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC, scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "자부담금"})]],
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True, max_vector_chunks=8),
        guardrail_chain=["faithfulness"],
        system_prompt="보험 상담",
    )

    events = []
    async for ev in executor.execute_stream(
        question="자부담금 얼마야", plan=plan, session_id="s1", trace=None, context=None,
    ):
        events.append(ev)

    assert llm.calls == 2 and guard.calls == 2
    steps = [e["data"].get("step") for e in events if e["type"] == "trace"]
    assert "regenerate" in steps
    # replace("") 후 재생성 답변 스트림
    idx = next(i for i, e in enumerate(events) if e["type"] == "replace" and e["data"] == "")
    final = "".join(e["data"] for e in events[idx + 1:] if e["type"] == "token")
    assert final == "중 큰 금액 기준 답변"
    done = next(e for e in events if e["type"] == "done")
    assert done["data"]["faithfulness_score"] == 1.0
