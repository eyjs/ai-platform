"""3-Tier Profile Router: 패턴 -> 키워드 스코어링 -> LLM.

프로필의 intent_hints, domain_scopes, description에서 키워드를 추출하여
Rule-based(Tier 1) -> Keyword Scoring(Tier 2) -> LLM(Tier 3) 순서로
프로필을 결정한다. 80%+ 질문이 Tier 1~2에서 <5ms 내에 해결된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RouteResult:
    """ProfileRouter 라우팅 결과."""

    profile_id: str
    reason: str
    tier: int  # 1, 2, 3
    confidence: float  # 0.0 ~ 1.0
    is_greeting: bool = False


# 인사/작별 패턴 (문장 시작 기준)
_GREETING_PATTERNS = re.compile(
    r"^(안녕|하이|헬로|hi|hello|ㅎㅇ)(?:하세요|요|~|!|$)"
    r"|^(고마워|감사합니다|감사해요|감사드|thanks|thank you)"
    r"|^(잘가|바이바이|bye|수고|다음에)",
    re.IGNORECASE,
)
_GREETING_MAX_LEN = 15


def _is_greeting(q: str) -> bool:
    """인사/작별 패턴 감지. ai-worker의 검증된 로직 재사용."""
    return len(q) <= _GREETING_MAX_LEN and bool(_GREETING_PATTERNS.search(q))


def _tokenize_korean(text: str) -> list[str]:
    """간단한 한국어 토큰화 (공백 + 한글/영문 2자 이상)."""
    return re.findall(r"[가-힣a-zA-Z]{2,}", text)


class ProfileRouter:
    """3-Tier 프로필 라우터.

    프로필 YAML의 intent_hints, domain_scopes, description에서
    키워드를 동적으로 추출하여 라우팅한다. 하드코딩 없음.
    """

    def __init__(self, profiles: list[dict]):
        self._profiles = profiles
        self._keyword_map: dict[str, list[tuple[str, float]]] = {}
        self._build_keyword_index()

    def _build_keyword_index(self) -> None:
        """프로필별 키워드 인덱스를 구축한다."""
        for p in self._profiles:
            keywords: list[tuple[str, float]] = []

            # intent_hints patterns (최고 가중치 1.0)
            for hint in p.get("intent_hints", []):
                for pattern in hint.get("patterns", []):
                    keywords.append((pattern, 1.0))

            # domain_scopes (중간 가중치 0.8)
            for domain in p.get("domain_scopes", []):
                keywords.append((domain, 0.8))

            # description 토큰 (낮은 가중치 0.3)
            desc = p.get("description", "")
            for token in _tokenize_korean(desc):
                if len(token) >= 2:
                    keywords.append((token, 0.3))

            self._keyword_map[p["id"]] = keywords

    # ── Tier 1: Rule-based (<1ms) ──

    def tier1_rule_match(self, question: str) -> RouteResult | None:
        """패턴 매칭으로 프로필을 결정한다."""
        q = question.strip()

        # 1-a. 인사/작별 감지
        if _is_greeting(q):
            general = self._find_profile("general-chat") or self._profiles[0]["id"]
            return RouteResult(
                general, "인사/작별", tier=1, confidence=0.95, is_greeting=True,
            )

        # 1-b. intent_hints patterns 매칭 (2자 이상 패턴만, 앞뒤 경계 체크)
        for p in self._profiles:
            for hint in p.get("intent_hints", []):
                for pattern in hint.get("patterns", []):
                    if len(pattern) < 2:
                        continue
                    # 다중 단어 패턴("만드는 법")은 substring, 단일 단어는 경계 체크
                    if " " in pattern:
                        if pattern in q:
                            return RouteResult(
                                p["id"],
                                f"키워드 매칭: {pattern}",
                                tier=1,
                                confidence=0.9,
                            )
                    elif re.search(rf"(?<![가-힣a-zA-Z]){re.escape(pattern)}(?![가-힣a-zA-Z])", q):
                        # 단일 단어: 앞뒤가 한글/영문이 아닌 경우만 매칭
                        return RouteResult(
                            p["id"],
                            f"키워드 매칭: {pattern}",
                            tier=1,
                            confidence=0.9,
                        )
                    elif pattern in q:
                        # 경계 매칭 실패 시 substring 폴백 (3자 이상만)
                        if len(pattern) >= 3:
                            return RouteResult(
                                p["id"],
                                f"키워드 매칭: {pattern}",
                                tier=1,
                                confidence=0.85,
                            )

        return None

    # ── Tier 2: Keyword Scoring (<5ms) ──

    def tier2_keyword_score(self, question: str) -> RouteResult | None:
        """키워드 스코어링으로 프로필을 결정한다."""
        q_tokens = set(_tokenize_korean(question))
        scores: dict[str, float] = {}

        for profile_id, keywords in self._keyword_map.items():
            score = 0.0
            for keyword, weight in keywords:
                # 원문에 키워드 포함 또는 토큰 정확 매칭
                if keyword in question or keyword in q_tokens:
                    score += weight
            if score > 0:
                scores[profile_id] = score

        if not scores:
            return None

        sorted_scores = sorted(scores.values(), reverse=True)
        best_score = sorted_scores[0]
        if best_score < 0.5:
            return None

        best = max(scores, key=scores.get)
        # 2위 대비 gap 기반 confidence: 2위가 없거나 격차가 크면 높은 confidence
        second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0.0
        total = sum(scores.values())
        confidence = best_score / total if total > 0 else 0.0

        if confidence >= 0.5:
            return RouteResult(
                best,
                f"키워드 스코어: {confidence:.2f}",
                tier=2,
                confidence=confidence,
            )

        return None

    # ── 유틸리티 ──

    def get_keywords(self, profile_id: str) -> list[tuple[str, float]]:
        """프로필의 키워드 목록을 반환한다. Continuation Router에서 사용."""
        return self._keyword_map.get(profile_id, [])

    def _find_profile(self, profile_id: str) -> str | None:
        """프로필 ID가 목록에 존재하는지 확인한다."""
        for p in self._profiles:
            if p["id"] == profile_id:
                return profile_id
        return None
