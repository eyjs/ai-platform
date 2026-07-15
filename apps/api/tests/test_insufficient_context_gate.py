"""관련도 게이트 — needs_rag인데 컨텍스트가 없으면 정직 반려(환각 방지)."""

from dataclasses import dataclass

from src.agent.executors._helpers import insufficient_context_refusal


@dataclass
class _Strategy:
    needs_rag: bool


@dataclass
class _Plan:
    strategy: _Strategy


def test_refuses_when_needs_rag_and_empty():
    plan = _Plan(_Strategy(needs_rag=True))
    msg = insufficient_context_refusal(plan, [])
    assert msg is not None and "자료를 찾지 못" in msg


def test_no_refusal_when_context_present():
    plan = _Plan(_Strategy(needs_rag=True))
    assert insufficient_context_refusal(plan, [{"chunk_id": "a"}]) is None


def test_no_refusal_when_rag_not_needed():
    """일반 대화(needs_rag=False)는 컨텍스트가 없어도 정상 — 게이트하지 않는다."""
    plan = _Plan(_Strategy(needs_rag=False))
    assert insufficient_context_refusal(plan, []) is None
