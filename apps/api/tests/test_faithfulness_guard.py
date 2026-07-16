"""FaithfulnessGuard 테스트: quick_check + deep_eval."""

import pytest
from unittest.mock import AsyncMock

from src.safety.base import GuardrailContext
from src.safety.faithfulness import FaithfulnessGuard


def _ctx(answer_docs: list[dict], policy: str = "balanced") -> GuardrailContext:
    # prompt_documents = 프롬프트에 [1]..[N]으로 실린 청크(순서=번호).
    # 인용 검사는 이걸 본다 — 여기선 소스 전체가 그대로 프롬프트에 실렸다고 본다.
    return GuardrailContext(
        question="보험금 청구",
        source_documents=answer_docs,
        prompt_documents=answer_docs,
        response_policy=policy,
    )


def _chunk(content: str, file_name: str = "test.pdf") -> dict:
    return {"content": content, "file_name": file_name, "title": "test"}


# --- Quick-check: 숫자 co-occurrence ---


@pytest.mark.asyncio
async def test_numbers_cooccur_in_same_chunk_passes():
    """같은 청크에 '8급'과 '200만원'이 함께 있으면 pass."""
    guard = FaithfulnessGuard()
    docs = [_chunk("상해 8급에 해당하며 200만원을 지급합니다.")]
    result = await guard.check("8급 상해는 200만원입니다.", _ctx(docs))
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_numbers_in_different_chunks_warns():
    """'8급'과 '200만원'이 다른 청크에 있으면 warn."""
    guard = FaithfulnessGuard()
    docs = [
        _chunk("상해 8급에 해당합니다."),
        _chunk("보험금 200만원을 지급합니다."),
    ]
    result = await guard.check("8급 상해는 200만원입니다.", _ctx(docs))
    assert result.action == "warn"
    assert "공존" in (result.reason or "")


@pytest.mark.asyncio
async def test_single_number_always_passes():
    """숫자가 1개만 있으면 co-occurrence 체크 불필요."""
    guard = FaithfulnessGuard()
    docs = [_chunk("보험금 200만원을 지급합니다.")]
    result = await guard.check("200만원입니다.", _ctx(docs))
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_numbers_not_in_source_warns():
    """기존 동작: 소스에 없는 숫자는 warn."""
    guard = FaithfulnessGuard()
    docs = [_chunk("상해 8급에 해당합니다.")]
    result = await guard.check("8급 상해는 500만원입니다.", _ctx(docs))
    assert result.action == "warn"


# --- Quick-check: 인용 검증 ---


@pytest.mark.asyncio
async def test_citation_exists_in_source_passes():
    """응답이 언급한 문서명이 소스에 있으면 pass."""
    guard = FaithfulnessGuard()
    docs = [_chunk("내용입니다.", file_name="상해등급표.pdf")]
    result = await guard.check("상해등급표.pdf에 따르면 해당됩니다.", _ctx(docs))
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_citation_out_of_range_warns():
    """프롬프트에 없던 번호를 인용하면 warn — 지어낸 출처.

    인용 계약이 파일명 문자열에서 번호로 바뀌었다(2026-07-16). 문자열 대조는
    파일명에 공백이 있으면 정확히 인용해도 실패해 오탐만 냈다.
    """
    guard = FaithfulnessGuard()
    docs = [_chunk("내용입니다.", file_name="상해등급표.pdf")]
    result = await guard.check("[5]에 따르면 해당됩니다.", _ctx(docs))
    assert result.action == "warn"
    assert "인용" in (result.reason or "")


@pytest.mark.asyncio
async def test_no_citation_in_answer_passes():
    """응답에 [n] 인용 토큰이 없으면 인용 체크 스킵."""
    guard = FaithfulnessGuard()
    docs = [_chunk("내용입니다.", file_name="상해등급표.pdf")]
    result = await guard.check("해당 사항이 없습니다.", _ctx(docs))
    assert result.action == "pass"


# --- Deep Eval (LLM, STRICT only) ---


@pytest.mark.asyncio
async def test_deep_eval_skipped_for_balanced():
    """balanced 정책이면 deep_eval 실행하지 않음."""
    llm = AsyncMock()
    guard = FaithfulnessGuard(router_llm=llm)
    docs = [_chunk("내용입니다.")]
    result = await guard.check("답변입니다.", _ctx(docs, policy="balanced"))
    llm.generate_json.assert_not_called()
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_deep_eval_runs_for_strict():
    """strict 정책이면 deep_eval 실행."""
    llm = AsyncMock()
    llm.generate_json.return_value = {"faithful": True}
    guard = FaithfulnessGuard(router_llm=llm)
    docs = [_chunk("8급 상해는 200만원입니다.")]
    result = await guard.check("8급은 200만원.", _ctx(docs, policy="strict"))
    llm.generate_json.assert_called_once()
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_deep_eval_unfaithful_warns():
    """LLM이 근거 불충분 판정하면 warn."""
    llm = AsyncMock()
    llm.generate_json.return_value = {"faithful": False, "reason": "근거 없음"}
    guard = FaithfulnessGuard(router_llm=llm)
    docs = [_chunk("8급 상해에 해당합니다.")]
    result = await guard.check("해당 상해입니다.", _ctx(docs, policy="strict"))
    assert result.action == "warn"
    assert "근거" in (result.reason or "")


@pytest.mark.asyncio
async def test_deep_eval_llm_failure_passes():
    """LLM 호출 실패 시 graceful degradation — pass."""
    llm = AsyncMock()
    llm.generate_json.side_effect = Exception("LLM down")
    guard = FaithfulnessGuard(router_llm=llm)
    docs = [_chunk("내용입니다.")]
    result = await guard.check("답변입니다.", _ctx(docs, policy="strict"))
    assert result.action == "pass"


# --- 엣지 케이스 ---


@pytest.mark.asyncio
async def test_no_source_documents_passes():
    """소스 문서가 없으면 pass."""
    guard = FaithfulnessGuard()
    result = await guard.check("답변입니다.", _ctx([]))
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_no_numbers_in_answer_passes():
    """답변에 숫자가 없으면 pass."""
    guard = FaithfulnessGuard()
    docs = [_chunk("상해에 해당합니다.")]
    result = await guard.check("해당 사항이 없습니다.", _ctx(docs))
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_no_llm_skips_deep_eval():
    """router_llm 없으면 deep_eval 스킵."""
    guard = FaithfulnessGuard(router_llm=None)
    docs = [_chunk("8급 상해는 200만원입니다.")]
    result = await guard.check("8급은 200만원.", _ctx(docs, policy="strict"))
    assert result.action == "pass"


# --- 회귀: 전역 카운터 자기-비활성화 버그 ---


@pytest.mark.asyncio
async def test_not_self_disabled_after_repeated_checks():
    """버그 회귀 방지: 동일 guard 인스턴스로 여러 번 호출해도 매번 검증한다.

    과거엔 GuardrailContext에 session_id가 없어 전역 'default' 카운터가 누적 →
    3회 초과 시 score=None(검증 비활성)이었다. 이제 매 요청 정상 동작해야 한다.
    """
    guard = FaithfulnessGuard()
    docs = [_chunk("상해 8급에 해당합니다.")]
    # 소스에 없는 숫자(500만원) → 매번 warn 되어야 함
    for i in range(6):
        result = await guard.check("8급 상해는 500만원입니다.", _ctx(docs))
        assert result.action == "warn", f"{i + 1}회째 검증이 비활성화됨(action={result.action})"
