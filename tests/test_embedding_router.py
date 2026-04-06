"""EmbeddingRouter 단위 테스트.

의도-능력 매칭 기반 프로필 라우터 검증:
- 프로필 초기화 + 임베딩
- 유사도 기반 라우팅
- 임계값 미달 → None
- 모호성(ambiguity) 감지
- 능력 세그먼트 추출
"""

import math

import pytest
from unittest.mock import AsyncMock

from src.orchestrator.embedding_router import (
    AMBIGUITY_GAP,
    SIMILARITY_THRESHOLD,
    EmbeddingRouteResult,
    EmbeddingRouter,
    _cosine_similarity,
)


# --- 헬퍼 ---


def _unit_vector(dim: int, index: int) -> list[float]:
    """index 위치만 1.0인 단위 벡터."""
    v = [0.0] * dim
    v[index] = 1.0
    return v


def _make_embedding_provider(dim: int = 4):
    """embed_batch가 호출 순서대로 단위 벡터를 반환하는 Mock."""
    provider = AsyncMock()
    provider.dimension = dim
    call_count = 0

    async def fake_embed_batch(texts: list[str]) -> list[list[float]]:
        nonlocal call_count
        result = []
        for _ in texts:
            result.append(_unit_vector(dim, call_count % dim))
            call_count += 1
        return result

    provider.embed_batch = AsyncMock(side_effect=fake_embed_batch)
    return provider


def _make_controlled_provider(mapping: dict[str, list[float]]):
    """텍스트 → 임베딩 매핑을 직접 제어하는 Mock."""
    provider = AsyncMock()

    async def fake_embed_batch(texts: list[str]) -> list[list[float]]:
        return [mapping.get(t, [0.0, 0.0, 0.0, 0.0]) for t in texts]

    provider.embed_batch = AsyncMock(side_effect=fake_embed_batch)
    return provider


# --- cosine_similarity 단위 테스트 ---


class TestCosineSimilarity:

    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert _cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_both_zero_returns_zero(self):
        assert _cosine_similarity([0.0], [0.0]) == 0.0


# --- 세그먼트 추출 ---


class TestExtractCapabilitySegments:

    def test_extracts_from_system_prompt(self):
        profile = {
            "id": "test",
            "system_prompt": "보험 상품에 대한 질문에 답변합니다\n짧은줄\n약관 해석 및 보상 기준을 설명합니다",
        }
        segments = EmbeddingRouter._extract_capability_segments(profile)
        assert "보험 상품에 대한 질문에 답변합니다" in segments
        assert "약관 해석 및 보상 기준을 설명합니다" in segments
        # 8자 미만 "짧은줄"은 제외
        assert "짧은줄" not in segments

    def test_extracts_description(self):
        profile = {"id": "test", "description": "보험 전문 챗봇입니다"}
        segments = EmbeddingRouter._extract_capability_segments(profile)
        assert "보험 전문 챗봇입니다" in segments

    def test_extracts_domain_scopes(self):
        profile = {"id": "test", "domain_scopes": ["insurance", "_common"]}
        segments = EmbeddingRouter._extract_capability_segments(profile)
        # _common은 제외
        assert "insurance" in segments

    def test_extracts_intent_hints(self):
        profile = {
            "id": "test",
            "intent_hints": [
                {"name": "coverage", "description": "보험 보상 범위에 대한 질문"},
                {"name": "short", "description": "짧음"},  # 5자 미만 제외
            ],
        }
        segments = EmbeddingRouter._extract_capability_segments(profile)
        assert "보험 보상 범위에 대한 질문" in segments
        assert "짧음" not in segments

    def test_deduplication(self):
        profile = {
            "id": "test",
            "system_prompt": "보험 상품에 대한 질문에 답변합니다",
            "description": "보험 상품에 대한 질문에 답변합니다",
        }
        segments = EmbeddingRouter._extract_capability_segments(profile)
        assert segments.count("보험 상품에 대한 질문에 답변합니다") == 1

    def test_empty_profile(self):
        segments = EmbeddingRouter._extract_capability_segments({"id": "empty"})
        assert segments == []

    def test_skips_markdown_headers(self):
        profile = {
            "id": "test",
            "system_prompt": "# 제목\n실제 능력 설명이 여기에 있습니다",
        }
        segments = EmbeddingRouter._extract_capability_segments(profile)
        assert not any(s.startswith("#") for s in segments)
        assert "실제 능력 설명이 여기에 있습니다" in segments


# --- 초기화 ---


class TestEmbeddingRouterInit:

    @pytest.mark.asyncio
    async def test_initialize_sets_flag(self):
        provider = _make_embedding_provider()
        router = EmbeddingRouter(provider)
        assert not router._initialized

        await router.initialize([
            {"id": "p1", "system_prompt": "보험 상품에 대한 전문 답변을 제공합니다"},
        ])
        assert router._initialized

    @pytest.mark.asyncio
    async def test_initialize_empty_profiles(self):
        provider = _make_embedding_provider()
        router = EmbeddingRouter(provider)
        await router.initialize([])
        assert not router._initialized

    @pytest.mark.asyncio
    async def test_initialize_embedding_failure(self):
        """임베딩 실패 시 초기화 건너뛰기 (에러 아님)."""
        provider = AsyncMock()
        provider.embed_batch = AsyncMock(side_effect=RuntimeError("GPU OOM"))
        router = EmbeddingRouter(provider)
        await router.initialize([
            {"id": "p1", "system_prompt": "보험 상품에 대한 전문 답변을 제공합니다"},
        ])
        assert not router._initialized

    @pytest.mark.asyncio
    async def test_initialize_stores_capabilities(self):
        provider = _make_embedding_provider(dim=8)
        router = EmbeddingRouter(provider)
        await router.initialize([
            {"id": "p1", "system_prompt": "보험에 대한 질문에 답변합니다\n약관 해석을 도와줍니다"},
            {"id": "p2", "description": "인사 관련 업무 지원 서비스입니다"},
        ])
        assert "p1" in router._profile_capabilities
        assert "p2" in router._profile_capabilities
        assert len(router._profile_capabilities["p1"]) == 2
        assert len(router._profile_capabilities["p2"]) == 1


# --- 라우팅 ---


class TestEmbeddingRouterRoute:

    @pytest.mark.asyncio
    async def test_not_initialized_returns_none(self):
        provider = _make_embedding_provider()
        router = EmbeddingRouter(provider)
        result = await router.route("보험료 알려줘")
        assert result is None

    @pytest.mark.asyncio
    async def test_route_returns_best_match(self):
        """질문 임베딩과 가장 유사한 프로필을 선택한다."""
        # p1: 보험 → [1,0,0,0], p2: HR → [0,1,0,0]
        # 질문: 보험 → [1,0,0,0] → p1 선택
        mapping = {
            "보험 상품에 대한 질문에 답변합니다": [1.0, 0.0, 0.0, 0.0],
            "인사 관련 업무 지원 서비스입니다": [0.0, 1.0, 0.0, 0.0],
            "보험료 얼마야": [0.95, 0.05, 0.0, 0.0],
        }
        provider = _make_controlled_provider(mapping)
        router = EmbeddingRouter(provider)
        await router.initialize([
            {"id": "insurance", "system_prompt": "보험 상품에 대한 질문에 답변합니다"},
            {"id": "hr", "description": "인사 관련 업무 지원 서비스입니다"},
        ])

        result = await router.route("보험료 얼마야")
        assert result is not None
        assert result.profile_id == "insurance"
        assert result.similarity > SIMILARITY_THRESHOLD

    @pytest.mark.asyncio
    async def test_below_threshold_returns_none(self):
        """모든 프로필 유사도가 임계값 미달이면 None."""
        # 질문과 모든 프로필이 직교
        mapping = {
            "보험 상품에 대한 질문에 답변합니다": [1.0, 0.0, 0.0, 0.0],
            "오늘 날씨 어때": [0.0, 0.0, 0.0, 1.0],
        }
        provider = _make_controlled_provider(mapping)
        router = EmbeddingRouter(provider)
        await router.initialize([
            {"id": "insurance", "system_prompt": "보험 상품에 대한 질문에 답변합니다"},
        ])

        result = await router.route("오늘 날씨 어때")
        assert result is None

    @pytest.mark.asyncio
    async def test_ambiguous_when_gap_small(self):
        """1위와 2위 차이가 AMBIGUITY_GAP 미만이면 ambiguous."""
        mapping = {
            "보험 상품에 대한 질문에 답변합니다": [0.8, 0.6, 0.0, 0.0],
            "인사 관련 업무 지원 서비스입니다": [0.79, 0.61, 0.0, 0.0],
            "보험 관련 질문입니다": [0.8, 0.6, 0.0, 0.0],
        }
        provider = _make_controlled_provider(mapping)
        router = EmbeddingRouter(provider)
        await router.initialize([
            {"id": "insurance", "system_prompt": "보험 상품에 대한 질문에 답변합니다"},
            {"id": "hr", "description": "인사 관련 업무 지원 서비스입니다"},
        ])

        result = await router.route("보험 관련 질문입니다")
        assert result is not None
        assert result.is_ambiguous is True

    @pytest.mark.asyncio
    async def test_embedding_error_returns_none(self):
        """라우팅 중 임베딩 에러 → None (크래시 아님)."""
        provider = _make_embedding_provider()
        router = EmbeddingRouter(provider)
        await router.initialize([
            {"id": "p1", "system_prompt": "보험 상품에 대한 질문에 답변합니다"},
        ])

        # route 시 임베딩 실패
        provider.embed_batch = AsyncMock(side_effect=RuntimeError("network"))
        result = await router.route("아무 질문")
        assert result is None

    @pytest.mark.asyncio
    async def test_result_fields(self):
        """EmbeddingRouteResult 필드가 올바르게 채워진다."""
        mapping = {
            "보험 상품에 대한 질문에 답변합니다": [1.0, 0.0, 0.0, 0.0],
            "보험료 알려줘": [0.99, 0.01, 0.0, 0.0],
        }
        provider = _make_controlled_provider(mapping)
        router = EmbeddingRouter(provider)
        await router.initialize([
            {"id": "insurance", "system_prompt": "보험 상품에 대한 질문에 답변합니다"},
        ])

        result = await router.route("보험료 알려줘")
        assert isinstance(result, EmbeddingRouteResult)
        assert result.profile_id == "insurance"
        assert 0.0 <= result.similarity <= 1.0
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.is_ambiguous, bool)
        assert len(result.matched_capability) > 0
