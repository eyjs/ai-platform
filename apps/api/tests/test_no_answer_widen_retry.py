"""무답변 확장 재시도 — 경계선 정원 컷 비결정 오답의 결정론적 복구.

답변이 "정보 부재" 정형 문구면 검색 정원을 2배로 넓혀 1회 재실행한다.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.executors._helpers import is_no_answer, is_no_answer_dominant, widen_plan
from src.agent.graph_executor import GraphExecutor
from src.domain.execution_plan import (
    ExecutionPlan, QuestionStrategy, QuestionType, ToolCall,
)
from src.domain.models import AgentMode, SearchScope
from src.tools.base import ToolResult


# --- is_no_answer ---


class TestIsNoAnswer:
    def test_detects_canned_phrases(self):
        assert is_no_answer("해당 내용은 확인이 필요합니다. 보험사에 문의하세요.")
        assert is_no_answer("제공된 문서에는 관련 정보가 포함되어 있지 않습니다.")
        assert is_no_answer("가입 조건이 명시되어 있지 않습니다.")

    def test_normal_answer_not_flagged(self):
        assert not is_no_answer("가입 나이는 만 15세부터 70세까지입니다.")
        assert not is_no_answer("")


class TestIsNoAnswerDominant:
    """무답변 "지배" 판정 — 장문 답변의 말미 관용구는 재시도 트리거가 아니다."""

    def test_short_hedge_answer_is_dominant(self):
        """짧은 "확인 필요" 답변 = 실질 무답변 → 재시도 유효."""
        assert is_no_answer_dominant("해당 내용은 확인이 필요합니다. 보험사에 문의하세요.")

    def test_refusal_opening_is_dominant(self):
        """서두부터 부재 선언(없는 상품 등) → 재시도 유효."""
        text = "제공된 문서에는 해당 상품 정보가 포함되어 있지 않습니다. " + "관련 상품 안내: " + "가" * 400
        assert is_no_answer_dominant(text)

    def test_long_answer_with_tail_hedge_not_dominant(self):
        """장문 실질 답변 + 말미 관용구 → 답변 보존 (재시도 안 함).

        구조적 교훈: 이미 답변하던 것을 강제로 끊고 검색 루프를 재실행하면
        멀쩡한 답변을 버리고 같은 모델로 재굴림 — 순수 낭비.
        """
        body = "공제금액은 10만원과 보상대상의료비의 30% 중 큰 금액입니다. " * 12
        text = body + "기타 세부 항목은 확인이 필요합니다."
        assert len(text) > 350
        assert not is_no_answer_dominant(text)

    def test_no_marker_not_dominant(self):
        assert not is_no_answer_dominant("가입 나이는 만 15세부터 70세까지입니다.")
        assert not is_no_answer_dominant("")


class TestWidenPlan:
    def test_doubles_chunks_with_cap(self):
        plan = ExecutionPlan(
            mode=AgentMode.DETERMINISTIC, scope=SearchScope(),
            strategy=QuestionStrategy(needs_rag=True, max_vector_chunks=8),
        )
        widened = widen_plan(plan)
        assert widened.strategy.max_vector_chunks == 16
        assert plan.strategy.max_vector_chunks == 8  # 원본 불변
        assert widen_plan(widened).strategy.max_vector_chunks == 16  # cap


# --- 비스트리밍 확장 재시도 ---


def _make_rag_plan(chunks: int = 8) -> ExecutionPlan:
    return ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "가입 자격"})]],
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True, max_vector_chunks=chunks),
        system_prompt="보험 전문가입니다.",
    )


def _make_executor(llm):
    from src.tools.base import ToolResult as TR
    registry = AsyncMock()
    registry.get = MagicMock(return_value=None)
    registry.execute = AsyncMock(return_value=TR(
        success=True,
        data=[{"document_id": "d1", "title": "요약서", "content": "가입나이 5~90세", "score": 0.9}],
    ))
    return GraphExecutor(main_llm=llm, tool_registry=registry, guardrails={})


@pytest.mark.asyncio
async def test_nonstream_widen_retry_on_no_answer():
    """첫 생성이 무답변이면 넓혀 재실행하고 두 번째 답을 반환한다."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(side_effect=[
        "해당 내용은 확인이 필요합니다.",
        "가입 나이는 5~90세입니다.",
    ])
    executor = _make_executor(mock_llm)

    response = await executor.execute("가입 자격 알려줘", _make_rag_plan(), "sess-1")

    assert response.answer == "가입 나이는 5~90세입니다."
    assert mock_llm.generate.call_count == 2


@pytest.mark.asyncio
async def test_nonstream_no_retry_on_real_answer():
    """정상 답변이면 재시도 없음 (생성 1회)."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="가입 나이는 5~90세입니다.")
    executor = _make_executor(mock_llm)

    response = await executor.execute("가입 자격 알려줘", _make_rag_plan(), "sess-1")

    assert response.answer == "가입 나이는 5~90세입니다."
    assert mock_llm.generate.call_count == 1


@pytest.mark.asyncio
async def test_nonstream_widened_still_no_answer_is_honest():
    """넓혀도 무답변이면 그대로 반환(재시도 1회 제한) — 정직 답변 유지."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="해당 내용은 확인이 필요합니다.")
    executor = _make_executor(mock_llm)

    response = await executor.execute("가입 자격 알려줘", _make_rag_plan(), "sess-1")

    assert "확인이 필요합니다" in response.answer
    assert mock_llm.generate.call_count == 2  # 원판 + 재시도 1회


# --- 스트리밍 확장 재시도 ---


class _FakeStreamLLM:
    """generate_stream_typed 호출마다 다른 답을 스트리밍하는 페이크."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = 0

    async def generate_stream_typed(self, prompt, system="", cacheable_system="",
                                    volatile_system="", max_tokens=None):
        from src.infrastructure.providers.base import StreamChunk
        answer = self._answers[min(self.calls, len(self._answers) - 1)]
        self.calls += 1
        for token in (answer[: len(answer) // 2], answer[len(answer) // 2:]):
            yield StreamChunk(kind="answer", content=token)


@pytest.mark.asyncio
async def test_stream_widen_retry_replaces_no_answer():
    """스트림: 무답변 후 replace("")로 비우고 재시도 답변을 스트리밍한다."""
    llm = _FakeStreamLLM([
        "해당 내용은 확인이 필요합니다.",
        "가입 나이는 5~90세입니다.",
    ])
    executor = _make_executor(llm)

    events = []
    async for ev in executor.execute_stream(
        question="가입 자격 알려줘", plan=_make_rag_plan(), session_id="sess-1",
        trace=None, context=None,
    ):
        events.append(ev)

    assert llm.calls == 2
    # widen_retry trace + replace("") 후 새 토큰이 흐른다
    steps = [e["data"].get("step") for e in events if e["type"] == "trace"]
    assert "widen_retry" in steps
    replaces = [e for e in events if e["type"] == "replace"]
    assert any(e["data"] == "" for e in replaces)
    # 최종 조립 답변 = 재시도 답
    idx = next(i for i, e in enumerate(events) if e["type"] == "replace" and e["data"] == "")
    final = "".join(e["data"] for e in events[idx + 1:] if e["type"] == "token")
    assert final == "가입 나이는 5~90세입니다."
    assert any(e["type"] == "done" for e in events)


@pytest.mark.asyncio
async def test_stream_no_retry_on_real_answer():
    llm = _FakeStreamLLM(["가입 나이는 5~90세입니다."])
    executor = _make_executor(llm)

    events = []
    async for ev in executor.execute_stream(
        question="가입 자격 알려줘", plan=_make_rag_plan(), session_id="sess-1",
        trace=None, context=None,
    ):
        events.append(ev)

    assert llm.calls == 1
    steps = [e["data"].get("step") for e in events if e["type"] == "trace"]
    assert "widen_retry" not in steps
