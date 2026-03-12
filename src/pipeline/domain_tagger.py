"""Domain Tagger: 문서에 도메인 코드 자동 태깅.

향후 구현: LLM 기반 도메인 분류.
현재: 수동 태깅 (ingest API에서 domain_code 직접 지정).
"""

import logging

logger = logging.getLogger(__name__)


class DomainTagger:
    """도메인 자동 태깅 (향후 구현)."""

    async def tag(self, content: str, file_name: str = "") -> str:
        """문서 내용을 분석하여 도메인 코드를 반환한다.

        현재는 미구현 — ingest API에서 domain_code를 직접 지정.
        """
        return "general"
