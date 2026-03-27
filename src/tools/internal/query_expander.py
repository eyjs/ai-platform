"""LLM 기반 쿼리 확장. 원본 쿼리를 변형하여 검색 재현율 향상."""

from src.locale.bundle import get_locale
from src.observability.logging import get_logger

logger = get_logger(__name__)

MAX_VARIANTS = 2


async def expand_queries(llm, query: str) -> list[str]:
    """원본 + 변형 쿼리 반환. 실패 시 원본만."""
    try:
        result = await llm.generate_json(
            get_locale().prompt("query_expander", query=query, max_variants=MAX_VARIANTS),
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
