"""LLM 기반 쿼리 확장. 원본 쿼리를 변형하여 검색 재현율 향상."""

from src.observability.logging import get_logger

logger = get_logger(__name__)

MAX_VARIANTS = 2

_EXPAND_PROMPT = """원본 질문을 분석하여 검색에 유용한 변형 쿼리 {max_variants}개를 생성하세요.
- 동의어/유사 표현 사용
- 구체적 <-> 일반적 관점 전환
- JSON 배열로만 응답: ["변형1", "변형2"]

원본: {query}"""


async def expand_queries(llm, query: str) -> list[str]:
    """원본 + 변형 쿼리 반환. 실패 시 원본만."""
    try:
        result = await llm.generate_json(
            _EXPAND_PROMPT.format(query=query, max_variants=MAX_VARIANTS),
        )

        if not isinstance(result, list):
            logger.warning("query_expander_invalid_format", type=type(result).__name__)
            return [query]

        variants = [q for q in result if isinstance(q, str) and q.strip()]
        variants = variants[:MAX_VARIANTS]

        logger.debug("query_expanded", original=query, variants=variants)
        return [query] + variants

    except Exception as e:
        logger.warning("query_expander_error", error=str(e))
        return [query]
