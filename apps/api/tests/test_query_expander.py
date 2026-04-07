"""쿼리 확장 단위 테스트."""

import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_returns_original_plus_variants():
    from src.tools.internal.query_expander import expand_queries
    llm = AsyncMock()
    llm.generate_json.return_value = ["보험금 청구 절차", "보험 클레임 방법"]
    result = await expand_queries(llm, "보험금 청구")
    assert result[0] == "보험금 청구"
    assert len(result) == 3


@pytest.mark.asyncio
async def test_limits_to_max_variants():
    from src.tools.internal.query_expander import expand_queries
    llm = AsyncMock()
    llm.generate_json.return_value = ["a", "b", "c", "d", "e"]
    result = await expand_queries(llm, "원본")
    assert len(result) == 3


@pytest.mark.asyncio
async def test_fallback_on_error():
    from src.tools.internal.query_expander import expand_queries
    llm = AsyncMock()
    llm.generate_json.side_effect = Exception("LLM timeout")
    result = await expand_queries(llm, "원본 질문")
    assert result == ["원본 질문"]


@pytest.mark.asyncio
async def test_fallback_on_invalid_json():
    from src.tools.internal.query_expander import expand_queries
    llm = AsyncMock()
    llm.generate_json.return_value = {"not": "a list"}
    result = await expand_queries(llm, "원본")
    assert result == ["원본"]


@pytest.mark.asyncio
async def test_filters_empty_strings():
    from src.tools.internal.query_expander import expand_queries
    llm = AsyncMock()
    llm.generate_json.return_value = ["", "유효한 변형", "  "]
    result = await expand_queries(llm, "원본")
    assert len(result) == 2
