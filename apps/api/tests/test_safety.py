"""Safety Guard 테스트."""

import pytest

from src.safety.base import GuardrailContext, GuardrailResult
from src.safety.faithfulness import FaithfulnessGuard
from src.safety.pii_filter import PIIFilterGuard
from src.safety.response_policy import ResponsePolicyGuard


def test_guardrail_result_passed():
    result = GuardrailResult.passed()
    assert result.action == "pass"


def test_guardrail_result_block():
    result = GuardrailResult.block("no docs")
    assert result.action == "block"
    assert result.reason == "no docs"


def test_guardrail_result_warn():
    result = GuardrailResult.warn("unverified", "modified answer")
    assert result.action == "warn"
    assert result.modified_answer == "modified answer"


@pytest.mark.asyncio
async def test_response_policy_strict_no_docs():
    guard = ResponsePolicyGuard()
    ctx = GuardrailContext(response_policy="strict", source_documents=[])
    result = await guard.check("답변입니다", ctx)
    assert result.action == "block"


@pytest.mark.asyncio
async def test_response_policy_balanced_no_docs():
    guard = ResponsePolicyGuard()
    ctx = GuardrailContext(response_policy="balanced", source_documents=[])
    result = await guard.check("답변입니다", ctx)
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_pii_filter_phone():
    guard = PIIFilterGuard()
    ctx = GuardrailContext()
    result = await guard.check("연락처: 010-1234-5678", ctx)
    assert result.action == "warn"
    assert "마스킹됨" in result.modified_answer


@pytest.mark.asyncio
async def test_pii_filter_clean():
    guard = PIIFilterGuard()
    ctx = GuardrailContext()
    result = await guard.check("보험 가입 안내입니다.", ctx)
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_faithfulness_no_numbers():
    guard = FaithfulnessGuard()
    ctx = GuardrailContext(source_documents=[{"content": "보험 약관입니다."}])
    result = await guard.check("보험 약관을 설명합니다.", ctx)
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_faithfulness_number_verified():
    guard = FaithfulnessGuard()
    ctx = GuardrailContext(source_documents=[{"content": "보험금은 1,000,000원입니다."}])
    result = await guard.check("보험금은 1,000,000원입니다.", ctx)
    assert result.action == "pass"


@pytest.mark.asyncio
async def test_faithfulness_number_unverified():
    guard = FaithfulnessGuard()
    ctx = GuardrailContext(source_documents=[{"content": "보험금은 500,000원입니다."}])
    result = await guard.check("보험금은 1,000,000원입니다.", ctx)
    assert result.action == "warn"
