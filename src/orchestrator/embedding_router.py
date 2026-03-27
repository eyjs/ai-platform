"""의도-능력 매칭 기반 프로필 라우터.

프로필의 system_prompt, description, domain 정보를 그대로 임베딩하여
질문과 의미적으로 가장 가까운 프로필을 선택한다.

- 하드코딩 문장 합성 없음 — 프로필 원문이 곧 능력 기술
- 프로필 YAML만 잘 쓰면 라우팅 자동
- 다국어 지원, 범용어 면역
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.infrastructure.providers.base import EmbeddingProvider
from src.observability.logging import get_logger

logger = get_logger(__name__)

SIMILARITY_THRESHOLD = 0.32
AMBIGUITY_GAP = 0.03


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class EmbeddingRouteResult:
    profile_id: str
    similarity: float
    confidence: float
    is_ambiguous: bool
    matched_capability: str


class EmbeddingRouter:
    """의도-능력 매칭 프로필 라우터."""

    def __init__(self, embedding_provider: EmbeddingProvider):
        self._embedding = embedding_provider
        self._profile_capabilities: dict[str, list[tuple[str, list[float]]]] = {}
        self._initialized = False

    async def initialize(self, profiles: list[dict]) -> None:
        """프로필 원문에서 능력 기술을 추출하고 임베딩한다."""
        all_texts: list[str] = []
        text_to_profile: list[tuple[str, str]] = []

        for p in profiles:
            profile_id = p["id"]
            segments = self._extract_capability_segments(p)

            for seg in segments:
                all_texts.append(seg)
                text_to_profile.append((seg, profile_id))

        if not all_texts:
            logger.warning("embedding_router_no_capabilities")
            return

        embeddings = await self._embedding.embed_batch(all_texts)

        for (text, profile_id), embedding in zip(text_to_profile, embeddings):
            if profile_id not in self._profile_capabilities:
                self._profile_capabilities[profile_id] = []
            self._profile_capabilities[profile_id].append((text, embedding))

        self._initialized = True
        total = sum(len(v) for v in self._profile_capabilities.values())
        logger.info(
            "embedding_router_initialized",
            profiles=len(self._profile_capabilities),
            total_capabilities=total,
        )

    async def route(self, question: str) -> EmbeddingRouteResult | None:
        """질문과 프로필 능력 간 의미 유사도로 라우팅."""
        if not self._initialized or not self._profile_capabilities:
            return None

        query_embeddings = await self._embedding.embed_batch([question])
        query_vec = query_embeddings[0]

        profile_scores: dict[str, tuple[float, str]] = {}
        for profile_id, entries in self._profile_capabilities.items():
            best_sim = -1.0
            best_cap = ""
            for cap_text, cap_vec in entries:
                sim = _cosine_similarity(query_vec, cap_vec)
                if sim > best_sim:
                    best_sim = sim
                    best_cap = cap_text
            profile_scores[profile_id] = (best_sim, best_cap)

        if not profile_scores:
            return None

        sorted_profiles = sorted(
            profile_scores.items(), key=lambda x: x[1][0], reverse=True,
        )

        best_id, (best_score, best_cap) = sorted_profiles[0]
        second_score = sorted_profiles[1][1][0] if len(sorted_profiles) > 1 else 0.0
        gap = best_score - second_score

        if best_score < SIMILARITY_THRESHOLD:
            logger.info(
                "embedding_route_below_threshold",
                best_profile=best_id,
                best_score=round(best_score, 4),
                threshold=SIMILARITY_THRESHOLD,
            )
            return None

        is_ambiguous = gap < AMBIGUITY_GAP
        confidence = min(1.0, 0.5 + (gap / 0.1) * 0.5) if not is_ambiguous else gap / AMBIGUITY_GAP * 0.5

        logger.info(
            "embedding_route_result",
            best_profile=best_id,
            best_score=round(best_score, 4),
            matched_cap=best_cap[:80],
            second_profile=sorted_profiles[1][0] if len(sorted_profiles) > 1 else "",
            second_score=round(second_score, 4),
            gap=round(gap, 4),
            is_ambiguous=is_ambiguous,
        )

        return EmbeddingRouteResult(
            profile_id=best_id,
            similarity=best_score,
            confidence=confidence,
            is_ambiguous=is_ambiguous,
            matched_capability=best_cap,
        )

    @staticmethod
    def _extract_capability_segments(profile: dict) -> list[str]:
        """프로필 원문에서 의미 있는 세그먼트를 추출한다.

        하드코딩 문장 합성 없음 — 프로필에 적힌 그대로 사용.
        """
        segments: list[str] = []

        # system_prompt: 프로필의 핵심 능력 기술이 여기에 있음
        system_prompt = profile.get("system_prompt", "")
        if system_prompt:
            for line in system_prompt.strip().split("\n"):
                line = line.strip().lstrip("- ").lstrip("0123456789. ")
                # 빈 줄, 마크다운 헤더, 짧은 줄 스킵
                if not line or line.startswith("#") or len(line) < 8:
                    continue
                segments.append(line)

        # description
        description = profile.get("description", "")
        if description and len(description) >= 8:
            segments.append(description)

        # domain_scopes — 도메인명 자체가 의미 담고 있음
        domains = [d for d in profile.get("domain_scopes", []) if d and d != "_common"]
        if domains:
            segments.append(" ".join(domains))

        # intent_hints의 description — 각 인텐트의 능력 설명
        for hint in profile.get("intent_hints", []):
            desc = hint.get("description", "")
            if desc and len(desc) >= 5:
                segments.append(desc)

        # 중복 제거
        seen: set[str] = set()
        unique: list[str] = []
        for seg in segments:
            if seg not in seen:
                seen.add(seg)
                unique.append(seg)

        return unique
