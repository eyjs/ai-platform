"""질문 기반 엔티티 메타필터 (깔때기 1단계, P2) 테스트."""

from unittest.mock import AsyncMock

import pytest

from src.domain.agent_context import AgentContext
from src.domain.models import SearchScope
from src.tools.internal.entity_filter import (
    EntityDocIndex, extract_aliases,
)

DOCS = [
    {"id": "d1", "file_name": "무배당 프로미라이프 New간편간병보험2601 보험약관.pdf", "title": ""},
    {"id": "d2", "file_name": "무배당 프로미라이프 New간편간병보험2601 상품요약서.pdf", "title": ""},
    {"id": "d3", "file_name": "무배당 프로미라이프 참좋은더보장간병보험2601 보험약관.pdf", "title": ""},
    {"id": "d4", "file_name": "무배당 프로미라이프 참좋은더보장간병보험2601 상품요약서.pdf", "title": ""},
    {"id": "d5", "file_name": "무배당 프로미라이프 New간편암건강보험2601 보험약관.pdf", "title": ""},
    {"id": "d6", "file_name": "무배당 프로미라이프 간편실손의료비보험(유병력자용)2604 상품요약서.pdf", "title": ""},
]


class TestExtractAliases:
    def test_product_name_with_and_without_code(self):
        aliases = extract_aliases("무배당 프로미라이프 New간편간병보험2601 보험약관.pdf")
        assert "new간편간병보험2601" in aliases
        assert "new간편간병보험" in aliases  # 말미 숫자 제거판
        assert "보험약관" in aliases

    def test_short_tokens_dropped(self):
        aliases = extract_aliases("가이드 v2.pdf")
        assert "v2" not in aliases


class TestEntityDocIndex:
    def _index(self):
        idx = EntityDocIndex()
        idx.build(DOCS)
        return idx

    def test_common_prefix_dropped_as_nondiscriminative(self):
        """전 문서 공통 토큰(무배당·프로미라이프)은 변별력 없어 인덱스에서 제외."""
        idx = self._index()
        m = idx.match("무배당 프로미라이프 상품 알려줘")
        assert m.doc_ids == set()

    def test_single_product_match(self):
        idx = self._index()
        m = idx.match("New간편간병보험 가입 나이 알려줘")
        assert m.doc_ids == {"d1", "d2"}

    def test_comparison_matches_both_products(self):
        idx = self._index()
        m = idx.match("New간편간병보험이랑 참좋은더보장간병보험 가입 조건 차이 비교해줘")
        assert {"d1", "d2", "d3", "d4"} <= m.doc_ids

    def test_whitespace_variation_absorbed(self):
        idx = self._index()
        m = idx.match("New 간편간병보험 조건은?")
        assert {"d1", "d2"} <= m.doc_ids

    def test_no_entity_no_filter(self):
        idx = self._index()
        m = idx.match("보험료 납입 면제 조건 알려줘")
        assert m.doc_ids == set()

    def test_parenthesized_product(self):
        idx = self._index()
        m = idx.match("간편실손의료비보험 유병력자용 가입 자격")
        assert "d6" in m.doc_ids


@pytest.mark.asyncio
async def test_rag_search_applies_entity_scope():
    """rag_search가 질문 엔티티로 allowed_doc_ids를 좁혀 검색한다."""
    from src.tools.internal.rag_search import RAGSearchTool

    embedder = AsyncMock()
    store = AsyncMock()
    store.list_document_names = AsyncMock(return_value=DOCS)
    tool = RAGSearchTool(embedding_provider=embedder, vector_store=store)

    captured = {}

    async def fake_pipeline(query, scope, top_k, min_rerank_score=None):
        captured["scope"] = scope
        return ([{"chunk_id": "c1", "document_id": "d1", "content": "가입나이", "score": 0.9}],
                [0.1], {"filter": {}})

    tool._execute_full_pipeline = fake_pipeline

    result = await tool.execute(
        {"query": "New간편간병보험 가입 나이"},
        AgentContext(session_id="s1"),
        SearchScope(domain_codes=["보험"]),
    )
    assert result.success
    assert set(captured["scope"].allowed_doc_ids) == {"d1", "d2"}
    ef = result.metadata["trace_detail"]["filter"]["entity_filter"]
    assert ef["docs"] == 2 and ef["fallback"] is False


@pytest.mark.asyncio
async def test_rag_search_fallback_when_filtered_empty():
    """필터 검색이 빈손이면 무필터로 폴백한다 (recall 보증)."""
    from src.tools.internal.rag_search import RAGSearchTool

    embedder = AsyncMock()
    store = AsyncMock()
    store.list_document_names = AsyncMock(return_value=DOCS)
    tool = RAGSearchTool(embedding_provider=embedder, vector_store=store)

    calls = []

    async def fake_pipeline(query, scope, top_k, min_rerank_score=None):
        calls.append(scope.allowed_doc_ids)
        if scope.allowed_doc_ids is not None:
            return ([], [0.1], {"filter": {}})  # 필터 검색 빈손
        return ([{"chunk_id": "c9", "document_id": "d9", "content": "x", "score": 0.5}],
                [0.1], {"filter": {}})

    tool._execute_full_pipeline = fake_pipeline

    result = await tool.execute(
        {"query": "New간편간병보험 관련"},
        AgentContext(session_id="s1"),
        SearchScope(),
    )
    assert result.success and result.metadata["chunks_found"] == 1
    assert calls[0] is not None and calls[1] is None  # 필터 → 무필터 순
    assert result.metadata["trace_detail"]["filter"]["entity_filter"]["fallback"] is True


@pytest.mark.asyncio
async def test_rag_search_respects_preset_doc_ids():
    """SAME_DOC 후속 등 이미 고정된 allowed_doc_ids는 덮지 않는다."""
    from src.tools.internal.rag_search import RAGSearchTool

    embedder = AsyncMock()
    store = AsyncMock()
    store.list_document_names = AsyncMock(return_value=DOCS)
    tool = RAGSearchTool(embedding_provider=embedder, vector_store=store)

    captured = {}

    async def fake_pipeline(query, scope, top_k, min_rerank_score=None):
        captured["scope"] = scope
        return ([{"chunk_id": "c1", "document_id": "dX", "content": "x", "score": 0.9}],
                [0.1], {"filter": {}})

    tool._execute_full_pipeline = fake_pipeline

    await tool.execute(
        {"query": "New간편간병보험 조건"},
        AgentContext(session_id="s1"),
        SearchScope(allowed_doc_ids=["dX"]),
    )
    assert captured["scope"].allowed_doc_ids == ["dX"]
    store.list_document_names.assert_not_awaited()


class TestIntersectionRule:
    """교집합 우선 결합 — 상품+유형은 정밀, 비교는 합집합."""

    def _index(self):
        idx = EntityDocIndex()
        idx.build(DOCS)
        return idx

    def test_product_plus_type_intersects_to_one_doc(self):
        """'A상품 약관' → 그 상품의 약관 1문서로 정밀 축소."""
        idx = self._index()
        m = idx.match("New간편간병보험 보험약관에서 청구 서류 알려줘")
        assert m.doc_ids == {"d1"}

    def test_comparison_disjoint_falls_back_to_union(self):
        """'A랑 B 비교' → 교집합 공집합 → 합집합 (양쪽 보장)."""
        idx = self._index()
        m = idx.match("New간편간병보험이랑 참좋은더보장간병보험 보험약관 비교")
        assert {"d1", "d2", "d3", "d4"} <= m.doc_ids

    def test_single_alias_unchanged(self):
        idx = self._index()
        m = idx.match("참좋은더보장간병보험 가입 조건")
        assert m.doc_ids == {"d3", "d4"}


class TestAsciiPrefixVariant:
    """브랜드 접두(New 등) 생략 질문도 매칭 — t3 케이스."""

    def _index(self):
        idx = EntityDocIndex()
        idx.build(DOCS)
        return idx

    def test_deprefixed_alias_extracted(self):
        aliases = extract_aliases("무배당 프로미라이프 New간편암건강보험2601 보험약관.pdf")
        assert "간편암건강보험" in aliases

    def test_query_without_brand_prefix_matches(self):
        idx = self._index()
        m = idx.match("간편암건강보험 가입나이 알려줘")
        assert "d5" in m.doc_ids  # New간편암건강보험 약관

    def test_deprefixed_does_not_cross_match_other_product(self):
        """"간편간병보험"(New 접두 제거판)이 참좋은더보장간병보험에 오매칭되지 않는다."""
        idx = self._index()
        m = idx.match("간편간병보험 조건 알려줘")
        assert m.doc_ids == {"d1", "d2"}  # New간편간병 문서만


class TestQualifierSuffixVariant:
    """한정사 접미 '용' 제거판 — "유병력자용" 문서를 "유병력자"로 매칭 (실사고 720)."""

    def _index(self):
        idx = EntityDocIndex()
        idx.build(DOCS)
        return idx

    def test_suffix_stripped_alias_extracted(self):
        aliases = extract_aliases(
            "무배당 프로미라이프 간편실손의료비보험(유병력자용)2604 상품요약서.pdf")
        assert "유병력자" in aliases

    def test_colloquial_condition_mention_matches(self):
        """"나 유병력자인데 실손..." 구어 질문이 실손(유병력자용) 문서로 좁혀진다."""
        idx = self._index()
        m = idx.match("나 유병력자인데 입원했거든 실손의료비 자부담금 궁금해")
        assert "d6" in m.doc_ids
