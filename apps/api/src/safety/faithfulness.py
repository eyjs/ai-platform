"""Faithfulness Guard: 숫자 co-occurrence + 인용 검증 + LLM deep eval.

Quick-check (항상):
  1. 숫자 멤버십 — 답변의 숫자가 소스에 존재하는지
  2. 숫자 co-occurrence — 답변의 숫자 쌍이 같은 청크에 공존하는지
  3. 인용 검증 — 답변이 언급한 문서명이 소스에 존재하는지

Deep eval (STRICT + router_llm 있을 때만):
  4. LLM 근거 검증 — "이 답변이 소스에 근거하는가?"
"""

import re
from itertools import combinations
from typing import Optional

from src.infrastructure.providers.base import LLMProvider
from src.locale.bundle import get_locale
from src.observability.logging import get_logger
from src.safety.base import GuardrailContext, GuardrailResult

logger = get_logger(__name__)


class FaithfulnessGuard:
    """숫자/인용 검증 가드레일."""

    name = "faithfulness"

    def __init__(self, router_llm: Optional[LLMProvider] = None):
        self._llm = router_llm
        exts = "|".join(get_locale().citation_extensions)
        self._citation_re = re.compile(rf'[\w가-힣]+\.(?:{exts})', re.IGNORECASE)

    async def check(self, answer: str, context: GuardrailContext) -> GuardrailResult:
        # 무한 재생성 방지는 nodes.py(_regen_count < 1, 요청당 재생성 1회)가 담당한다.
        # 과거의 세션별 전역 카운터는 GuardrailContext에 session_id가 없어 항상 'default'로
        # 누적 → 3회 후 영구 비활성화되는 버그였으므로 제거. faithfulness는 매 요청 동작한다.
        if not context.source_documents:
            # source_documents 가 없으면 측정 불가 → score=None
            return GuardrailResult.passed(score=None)

        # --- Quick-check 1: 숫자 멤버십 ---
        answer_numbers = self._extract_numbers(answer)
        if answer_numbers:
            source_text = " ".join(
                doc.get("content", "") for doc in context.source_documents
            )
            source_numbers = self._extract_numbers(source_text)
            unverified = [n for n in answer_numbers if n not in source_numbers]
            if unverified:
                # 완화 검증: 단위/접미사 제거 후 숫자값만 비교
                # "1항" → "1", 소스 "1." → "1" 이면 pass
                source_bare = self._extract_bare_numbers(source_text)
                still_unverified = [
                    n for n in unverified
                    if not set(re.findall(r'\d+', n)).issubset(source_bare)
                ]
                if still_unverified:
                    warning = get_locale().message("number_missing", numbers=str(still_unverified))
                    logger.warning("faithfulness_number_missing", detail=warning)
                    modified = answer + f"\n\n[주의: {warning}]"
                    # Quick-check 실패 → 0.5
                    return GuardrailResult.warn(warning, modified, score=0.5)

            # --- Quick-check 2: 숫자 co-occurrence ---
            if len(answer_numbers) >= 2:
                result = self._check_cooccurrence(answer_numbers, context.source_documents)
                if result:
                    return result

        # --- Quick-check 2.5: 계산 연산 왜곡 (max→합산 등) ---
        distortion = self._check_operator_distortion(answer, context.source_documents)
        if distortion:
            return distortion

        # --- Quick-check 3: 인용 검증 ---
        citation_result = self._check_citations(answer, context.source_documents)
        if citation_result:
            return citation_result

        # --- Deep eval (STRICT only) ---
        if context.response_policy == "strict" and self._llm:
            deep_result = await self._deep_eval(answer, context)
            if deep_result:
                return deep_result

        # 모든 검증 통과 → 1.0
        return GuardrailResult.passed(score=1.0)

    # 소스의 max 연산 패턴: "A와 B 중 큰 금액" (숫자·금액·비율 쌍)
    _MAX_PAIR_RE = re.compile(
        r'([\d,.]+\s*[만억천]?\s*원|[\d.]+\s*%)'
        r'\s*[과와]\s*.{0,40}?'
        r'([\d,.]+\s*[만억천]?\s*원|[\d.]+\s*%)'
        r'\s*중\s*큰'
    )
    # 답변의 합산 표현
    _SUM_WORD_RE = re.compile(r'합산|합한|합친|더한|더하여|더해서|\+')

    @classmethod
    def _check_operator_distortion(
        cls, answer: str, docs: list[dict],
    ) -> Optional[GuardrailResult]:
        """계산 연산 왜곡 검사 — 금전 도메인 치명 오류의 결정론 검출.

        소스가 "A와 B **중 큰 금액**"(max)인데 답변이 같은 숫자쌍을
        "합산"으로 서술하면 심각 위반(실사고: 실손 공제금액 max→합산 왜곡 —
        숫자 자체는 전부 소스에 있어 기존 숫자 검증을 전부 통과했다).
        """
        def norm(s: str) -> str:
            return re.sub(r'[\s,]', '', s)

        pairs = set()
        for doc in docs:
            for a, b in cls._MAX_PAIR_RE.findall(doc.get("content", "")):
                pairs.add((norm(a), norm(b)))
        if not pairs:
            return None

        answer_norm = re.sub(r'[\s,]', '', answer)
        for a, b in pairs:
            ia, ib = answer_norm.find(a), answer_norm.find(b)
            if ia < 0 or ib < 0:
                continue
            lo, hi = min(ia, ib), max(ia, ib) + max(len(a), len(b))
            window = answer_norm[max(0, lo - 20):hi + 20]
            if cls._SUM_WORD_RE.search(window) and '중큰' not in window:
                warning = (
                    f"원문은 '{a}'와 '{b}' 중 큰 금액(max) 연산인데 "
                    f"답변이 합산으로 서술 — 계산 왜곡"
                )
                logger.warning("faithfulness_operator_distortion", detail=warning)
                return GuardrailResult.warn(warning, None, score=0.2)
        return None

    @staticmethod
    def _check_cooccurrence(
        answer_numbers: set[str], docs: list[dict],
    ) -> Optional[GuardrailResult]:
        """답변의 숫자 쌍이 같은 청크에 공존하는지 검증."""
        for a, b in combinations(answer_numbers, 2):
            found_together = False
            for doc in docs:
                content = doc.get("content", "")
                if a in content and b in content:
                    found_together = True
                    break
            if not found_together:
                warning = get_locale().message("cooccurrence_fail", a=a, b=b)
                logger.warning("faithfulness_cooccurrence", detail=warning)
                return GuardrailResult.warn(
                    warning, None, score=0.5,
                )
        return None

    def _check_citations(self, answer: str, docs: list[dict]) -> Optional[GuardrailResult]:
        """답변에서 언급한 문서명이 소스에 존재하는지 검증."""
        cited = self._citation_re.findall(answer)
        if not cited:
            return None

        source_files = {
            doc.get("file_name", "") for doc in docs
        }
        for cite in cited:
            if cite not in source_files:
                warning = get_locale().message("citation_missing", cite=cite)
                logger.warning("faithfulness_citation", detail=warning)
                return GuardrailResult.warn(warning, None, score=0.5)
        return None

    async def _deep_eval(
        self, answer: str, context: GuardrailContext,
    ) -> Optional[GuardrailResult]:
        """LLM으로 근거 검증 (STRICT 전용)."""
        try:
            sources = "\n---\n".join(
                doc.get("content", "")[:500] for doc in context.source_documents[:5]
            )
            prompt = get_locale().prompt("deep_eval", sources=sources, answer=answer)
            result = await self._llm.generate_json(prompt)

            if not result.get("faithful", True):
                reason = result.get("reason", "근거 불충분")
                logger.warning("faithfulness_deep_eval", reason=reason)
                return GuardrailResult.warn(
                    get_locale().message("deep_eval_fail", reason=reason),
                    None,
                    score=0.3,
                )
        except Exception as e:
            logger.warning("faithfulness_deep_eval_error", error=str(e))
        return None

    @staticmethod
    def _extract_numbers(text: str) -> set[str]:
        """텍스트에서 의미 있는 숫자를 추출한다."""
        numbers = set()
        for pattern in get_locale().number_patterns:
            numbers.update(pattern.findall(text))
        # 단독 숫자 (3자리 이상만)
        for match in re.findall(r'\b(\d{3,})\b', text):
            numbers.add(match)
        return numbers

    @staticmethod
    def _extract_bare_numbers(text: str) -> set[str]:
        """숫자값만 추출 (단위/접미사 제거). 숫자 매칭 완화용."""
        return set(re.findall(r'\d+', text))
