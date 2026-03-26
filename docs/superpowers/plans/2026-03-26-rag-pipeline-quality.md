# RAG Pipeline 품질 강화 구현 계획

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ai-platform RAG 검색 품질을 ai-worker 수준 이상으로 강화한다 (5개 독립 레이어 + max_tokens 수정)

**Architecture:** rag_search.py를 순수 오케스트레이터로 변환하고, 쿼리확장/노이즈필터/이웃확장/리랭킹/결과가드를 각각 독립 모듈로 구현. 각 레이어는 `list[dict] -> list[dict]` 시그니처로 내부 교체 가능.

**Tech Stack:** Python 3.12, pytest, asyncio, httpx

**Spec:** `docs/superpowers/specs/2026-03-26-rag-pipeline-quality-design.md`

---

## Context

- 테스트 실행: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/ --ignore=tests/test_kms_client.py -x -v`
- ai-platform 소스 루트: `/Users/eyjs/Desktop/WorkSpace/ai-platform`
- 기존 테스트 패턴 참고: `tests/test_tools.py`, `tests/test_safety.py`
- RAGSearchTool은 `src/bootstrap.py:122-126`에서 생성되어 ToolRegistry에 등록

---

## 파일 구조

| 작업 | 파일 | 역할 |
|------|------|------|
| Create | `src/tools/internal/noise_filter.py` | L2. Score Gap 노이즈 필터 |
| Create | `src/tools/internal/neighbor_expander.py` | L3. 인접 청크 맥락 확장 |
| Create | `src/tools/internal/reranker_pipeline.py` | L4. 3-tier 리랭킹 + 융합 |
| Create | `src/tools/internal/result_guard.py` | L5. PII 마스킹 콘텐츠 가드 |
| Create | `src/tools/internal/query_expander.py` | L1. LLM 멀티쿼리 확장 |
| Modify | `src/tools/internal/rag_search.py` | 오케스트레이터로 리팩터링 |
| Modify | `src/infrastructure/providers/llm/http_llm.py` | max_tokens 파라미터 추가 |
| Modify | `src/config.py` | llm_max_tokens 환경변수 |
| Modify | `src/infrastructure/providers/factory.py:70-79` | max_tokens 전달 |
| Modify | `src/bootstrap.py:122-126` | router_llm 주입 |
| Create | `tests/test_noise_filter.py` | 노이즈 필터 단위 테스트 |
| Create | `tests/test_neighbor_expander.py` | 이웃 확장 단위 테스트 |
| Create | `tests/test_reranker_pipeline.py` | 리랭킹 단위 테스트 |
| Create | `tests/test_result_guard.py` | 결과 가드 단위 테스트 |
| Create | `tests/test_query_expander.py` | 쿼리 확장 단위 테스트 |
| Create | `tests/test_rag_search_pipeline.py` | 통합 파이프라인 테스트 |
| Create | `tests/test_http_llm_max_tokens.py` | max_tokens 전달 검증 |

---

## Chunk 1: 순수 함수 레이어 (L2 + L5)

외부 의존 없는 순수 함수 모듈부터 구현. 가장 빠르고 안전하게 검증 가능.

### Task 1: Noise Filter (L2)

**Files:**
- Create: `src/tools/internal/noise_filter.py`
- Create: `tests/test_noise_filter.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_noise_filter.py
"""노이즈 필터 단위 테스트."""


def _chunk(chunk_id: str, score: float) -> dict:
    return {"chunk_id": chunk_id, "score": score, "content": f"content-{chunk_id}"}


def test_empty_input():
    from src.tools.internal.noise_filter import filter_noise

    assert filter_noise([]) == []


def test_under_min_keep_returns_all():
    """MIN_KEEP_COUNT 이하면 전부 반환."""
    from src.tools.internal.noise_filter import filter_noise

    candidates = [_chunk(str(i), 0.01 - i * 0.005) for i in range(3)]
    result = filter_noise(candidates)
    assert len(result) == 3


def test_gap_filter_cuts_at_30_percent():
    """1등 대비 30% 이상 하락 시 절단."""
    from src.tools.internal.noise_filter import filter_noise

    candidates = [
        _chunk("a", 0.016),
        _chunk("b", 0.015),
        _chunk("c", 0.014),
        _chunk("d", 0.013),
        _chunk("e", 0.012),
        # 여기부터 gap > 30%
        _chunk("f", 0.009),
        _chunk("g", 0.005),
    ]
    result = filter_noise(candidates)
    assert len(result) == 5
    assert result[-1]["chunk_id"] == "e"


def test_min_keep_overrides_gap():
    """gap이 크더라도 최소 5개는 유지."""
    from src.tools.internal.noise_filter import filter_noise

    candidates = [
        _chunk("a", 0.020),
        _chunk("b", 0.019),
        _chunk("c", 0.010),  # gap > 30% but index < MIN_KEEP
        _chunk("d", 0.009),
        _chunk("e", 0.008),
        _chunk("f", 0.002),
    ]
    result = filter_noise(candidates)
    assert len(result) >= 5


def test_all_same_score():
    """동점이면 전부 반환."""
    from src.tools.internal.noise_filter import filter_noise

    candidates = [_chunk(str(i), 0.01) for i in range(10)]
    result = filter_noise(candidates)
    assert len(result) == 10


def test_zero_top_score():
    """최고 점수가 0이면 MIN_KEEP_COUNT만큼 반환."""
    from src.tools.internal.noise_filter import filter_noise

    candidates = [_chunk(str(i), 0.0) for i in range(8)]
    result = filter_noise(candidates)
    assert len(result) == 5
```

- [ ] **Step 2: 테스트 실행 -- 실패 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_noise_filter.py -x -v`
Expected: `ModuleNotFoundError: No module named 'src.tools.internal.noise_filter'`

- [ ] **Step 3: noise_filter.py 구현**

```python
# src/tools/internal/noise_filter.py
"""검색 결과 노이즈 필터링. 내부 전략을 자유롭게 교체/추가 가능."""

from src.observability.logging import get_logger

logger = get_logger(__name__)

RELATIVE_GAP_RATIO = 0.3
MIN_KEEP_COUNT = 5


def filter_noise(candidates: list[dict]) -> list[dict]:
    """노이즈 필터 파사드. 내부 전략 체이닝."""
    result = _score_gap_filter(candidates)
    return result


def _score_gap_filter(candidates: list[dict]) -> list[dict]:
    """상대적 점수 갭 필터링. 1등 대비 30% 이상 하락 시 절단."""
    if len(candidates) <= MIN_KEEP_COUNT:
        return candidates

    top_score = candidates[0]["score"]
    if top_score <= 0:
        return candidates[:MIN_KEEP_COUNT]

    cutoff = len(candidates)
    for i in range(1, len(candidates)):
        gap_ratio = (top_score - candidates[i]["score"]) / top_score
        if gap_ratio >= RELATIVE_GAP_RATIO and i >= MIN_KEEP_COUNT:
            cutoff = i
            break

    if cutoff < len(candidates):
        logger.debug(
            "noise_filter_gap",
            before=len(candidates),
            after=cutoff,
            top_score=top_score,
        )

    return candidates[:cutoff]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_noise_filter.py -x -v`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add src/tools/internal/noise_filter.py tests/test_noise_filter.py
git commit -m "feat: L2 노이즈 필터 — Score Gap 기반 저품질 청크 제거"
```

---

### Task 2: Result Guard (L5)

**Files:**
- Create: `src/tools/internal/result_guard.py`
- Create: `tests/test_result_guard.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_result_guard.py
"""결과 가드 단위 테스트."""


def _chunk(content: str) -> dict:
    return {"chunk_id": "c1", "score": 0.9, "content": content}


def test_empty_input():
    from src.tools.internal.result_guard import guard_results

    assert guard_results([]) == []


def test_no_pii_passes_through():
    from src.tools.internal.result_guard import guard_results

    chunks = [_chunk("보험약관 제1조 목적")]
    result = guard_results(chunks)
    assert result[0]["content"] == "보험약관 제1조 목적"


def test_masks_resident_number():
    from src.tools.internal.result_guard import guard_results

    chunks = [_chunk("주민번호: 900101-1234567 입니다")]
    result = guard_results(chunks)
    assert "[주민번호]" in result[0]["content"]
    assert "900101-1234567" not in result[0]["content"]


def test_masks_phone_number():
    from src.tools.internal.result_guard import guard_results

    chunks = [_chunk("연락처 010-1234-5678로 문의")]
    result = guard_results(chunks)
    assert "[전화번호]" in result[0]["content"]
    assert "010-1234-5678" not in result[0]["content"]


def test_masks_account_number():
    from src.tools.internal.result_guard import guard_results

    chunks = [_chunk("계좌 110-123-456789")]
    result = guard_results(chunks)
    assert "[계좌번호]" in result[0]["content"]
    assert "110-123-456789" not in result[0]["content"]


def test_masks_multiple_pii():
    from src.tools.internal.result_guard import guard_results

    chunks = [_chunk("홍길동 900101-1234567 전화 010-9999-8888")]
    result = guard_results(chunks)
    assert "[주민번호]" in result[0]["content"]
    assert "[전화번호]" in result[0]["content"]


def test_preserves_other_fields():
    from src.tools.internal.result_guard import guard_results

    chunks = [{"chunk_id": "c1", "score": 0.9, "content": "900101-1234567", "file_name": "doc.pdf"}]
    result = guard_results(chunks)
    assert result[0]["chunk_id"] == "c1"
    assert result[0]["score"] == 0.9
    assert result[0]["file_name"] == "doc.pdf"
```

- [ ] **Step 2: 테스트 실행 -- 실패 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_result_guard.py -x -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: result_guard.py 구현**

```python
# src/tools/internal/result_guard.py
"""검색 결과 가드. LLM에 전달하기 전 민감 콘텐츠 필터링."""

import re

from src.observability.logging import get_logger

logger = get_logger(__name__)

# 주민번호: 6자리-7자리 (두 번째 부분 첫 숫자 1~4)
_RE_RESIDENT = re.compile(r"\d{6}-[1-4]\d{6}")
# 휴대폰: 010/011/016/017/018/019-0000-0000
_RE_PHONE = re.compile(r"01[016789]-\d{3,4}-\d{4}")
# 계좌번호: 3~6자리-2~6자리-2~6자리
_RE_ACCOUNT = re.compile(r"\d{3,6}-\d{2,6}-\d{2,6}")


def guard_results(candidates: list[dict]) -> list[dict]:
    """가드 파사드. 내부 전략 체이닝."""
    return _pii_guard(candidates)


def _pii_guard(candidates: list[dict]) -> list[dict]:
    """개인정보 패턴이 포함된 청크를 마스킹."""
    guarded = []
    masked_count = 0
    for c in candidates:
        masked = _mask_pii(c["content"])
        if masked != c["content"]:
            masked_count += 1
        guarded.append({**c, "content": masked})

    if masked_count:
        logger.info("result_guard_pii_masked", count=masked_count)

    return guarded


def _mask_pii(text: str) -> str:
    """정규식 기반 PII 마스킹."""
    text = _RE_RESIDENT.sub("[주민번호]", text)
    text = _RE_PHONE.sub("[전화번호]", text)
    text = _RE_ACCOUNT.sub("[계좌번호]", text)
    return text
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_result_guard.py -x -v`
Expected: 7 passed

- [ ] **Step 5: 커밋**

```bash
git add src/tools/internal/result_guard.py tests/test_result_guard.py
git commit -m "feat: L5 결과 가드 — PII 패턴 마스킹 (주민번호/전화번호/계좌번호)"
```

---

## Chunk 2: 비동기 레이어 (L3 + L4)

VectorStore/Reranker에 의존하는 async 모듈. Mock 기반 테스트.

### Task 3: Neighbor Expander (L3)

**Files:**
- Create: `src/tools/internal/neighbor_expander.py`
- Create: `tests/test_neighbor_expander.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_neighbor_expander.py
"""이웃 확장 단위 테스트."""

import pytest
from unittest.mock import AsyncMock


def _chunk(chunk_id: str, doc_id: str, idx: int, score: float) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "chunk_index": idx,
        "score": score,
        "content": f"content-{chunk_id}",
    }


@pytest.mark.asyncio
async def test_empty_input():
    from src.tools.internal.neighbor_expander import expand_neighbors

    store = AsyncMock()
    result = await expand_neighbors(store, [])
    assert result == []
    store.get_neighbor_chunks.assert_not_called()


@pytest.mark.asyncio
async def test_expands_top_n():
    from src.tools.internal.neighbor_expander import expand_neighbors

    store = AsyncMock()
    store.get_neighbor_chunks.return_value = [
        {"chunk_id": "nb1", "document_id": "d1", "content": "neighbor", "chunk_index": 0},
    ]

    candidates = [_chunk("c1", "d1", 1, 0.9)]
    result = await expand_neighbors(store, candidates)

    assert len(result) == 2  # original + 1 neighbor
    assert result[1]["chunk_id"] == "nb1"
    assert result[1]["score"] == pytest.approx(0.9 * 0.8)


@pytest.mark.asyncio
async def test_no_duplicate_neighbors():
    """이미 있는 청크는 다시 추가하지 않는다."""
    from src.tools.internal.neighbor_expander import expand_neighbors

    store = AsyncMock()
    store.get_neighbor_chunks.return_value = [
        {"chunk_id": "c2", "document_id": "d1", "content": "already", "chunk_index": 2},
    ]

    candidates = [
        _chunk("c1", "d1", 1, 0.9),
        _chunk("c2", "d1", 2, 0.8),
    ]
    result = await expand_neighbors(store, candidates)
    chunk_ids = [c["chunk_id"] for c in result]
    assert chunk_ids.count("c2") == 1


@pytest.mark.asyncio
async def test_missing_chunk_index_skipped():
    """chunk_index가 없는 청크는 확장 스킵."""
    from src.tools.internal.neighbor_expander import expand_neighbors

    store = AsyncMock()
    candidates = [{"chunk_id": "c1", "document_id": "d1", "score": 0.9, "content": "x"}]
    result = await expand_neighbors(store, candidates)
    assert len(result) == 1
    store.get_neighbor_chunks.assert_not_called()


@pytest.mark.asyncio
async def test_only_top_n_expanded():
    """NEIGHBOR_EXPAND_TOP_N개만 확장."""
    from src.tools.internal.neighbor_expander import expand_neighbors, NEIGHBOR_EXPAND_TOP_N

    store = AsyncMock()
    store.get_neighbor_chunks.return_value = []

    candidates = [_chunk(f"c{i}", f"d{i}", i, 0.9 - i * 0.01) for i in range(10)]
    await expand_neighbors(store, candidates)

    assert store.get_neighbor_chunks.call_count == NEIGHBOR_EXPAND_TOP_N
```

- [ ] **Step 2: 테스트 실행 -- 실패 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_neighbor_expander.py -x -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: neighbor_expander.py 구현**

```python
# src/tools/internal/neighbor_expander.py
"""인접 청크 확장. 상위 청크의 앞뒤 맥락을 보강한다."""

from src.observability.logging import get_logger

logger = get_logger(__name__)

NEIGHBOR_EXPAND_TOP_N = 5
NEIGHBOR_SCORE_DECAY = 0.8


async def expand_neighbors(
    vector_store, candidates: list[dict],
) -> list[dict]:
    """상위 N개 청크의 인접 청크를 가져와 후보에 추가."""
    if not candidates:
        return candidates

    seen = {c["chunk_id"] for c in candidates}
    expanded = list(candidates)

    for chunk in candidates[:NEIGHBOR_EXPAND_TOP_N]:
        idx = chunk.get("chunk_index")
        if idx is None:
            continue

        neighbors = await vector_store.get_neighbor_chunks(
            chunk["document_id"], [idx - 1, idx + 1],
        )

        for nb in neighbors:
            if nb["chunk_id"] not in seen:
                nb["score"] = chunk["score"] * NEIGHBOR_SCORE_DECAY
                expanded.append(nb)
                seen.add(nb["chunk_id"])

    added = len(expanded) - len(candidates)
    if added:
        logger.debug("neighbor_expand", added=added, total=len(expanded))

    return expanded
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_neighbor_expander.py -x -v`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add src/tools/internal/neighbor_expander.py tests/test_neighbor_expander.py
git commit -m "feat: L3 이웃 확장 — 상위 청크 앞뒤 맥락 보강"
```

---

### Task 4: Reranker Pipeline (L4)

**Files:**
- Create: `src/tools/internal/reranker_pipeline.py`
- Create: `tests/test_reranker_pipeline.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_reranker_pipeline.py
"""3-tier 리랭킹 파이프라인 단위 테스트."""

import pytest
from unittest.mock import AsyncMock


def _chunk(chunk_id: str, score: float) -> dict:
    return {"chunk_id": chunk_id, "score": score, "content": f"content-{chunk_id}"}


@pytest.mark.asyncio
async def test_tier1_high_quality():
    """고품질 결과만 있을 때 PREFERRED_MIN_SCORE 이상만 반환."""
    from src.tools.internal.reranker_pipeline import rerank_3tier

    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": 0, "score": 0.9},
        {"index": 1, "score": 0.7},
        {"index": 2, "score": 0.3},
    ]
    candidates = [_chunk("a", 0.8), _chunk("b", 0.7), _chunk("c", 0.6)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)

    # 융합 점수: 0.7*reranker + 0.3*vector
    # a: 0.7*0.9 + 0.3*0.8 = 0.87
    # b: 0.7*0.7 + 0.3*0.7 = 0.70
    # c: 0.7*0.3 + 0.3*0.6 = 0.39 (< 0.5)
    assert len(result) == 2
    assert result[0]["chunk_id"] == "a"


@pytest.mark.asyncio
async def test_tier2_fallback():
    """고품질 없을 때 FALLBACK_MIN_SCORE 이상 반환."""
    from src.tools.internal.reranker_pipeline import rerank_3tier

    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": 0, "score": 0.3},
        {"index": 1, "score": 0.2},
        {"index": 2, "score": 0.01},
    ]
    candidates = [_chunk("a", 0.4), _chunk("b", 0.3), _chunk("c", 0.2)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)

    # a: 0.7*0.3 + 0.3*0.4 = 0.33 (> 0.15)
    # b: 0.7*0.2 + 0.3*0.3 = 0.23 (> 0.15)
    # c: 0.7*0.01 + 0.3*0.2 = 0.067 (< 0.15)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_tier3_last_resort():
    """모든 점수가 낮아도 최소 LAST_RESORT_COUNT개 반환."""
    from src.tools.internal.reranker_pipeline import rerank_3tier

    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": i, "score": 0.01} for i in range(5)
    ]
    candidates = [_chunk(str(i), 0.01) for i in range(5)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)

    assert len(result) == 3  # LAST_RESORT_COUNT


@pytest.mark.asyncio
async def test_top_k_limits_output():
    """top_k보다 많이 반환하지 않는다."""
    from src.tools.internal.reranker_pipeline import rerank_3tier

    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": i, "score": 0.9} for i in range(10)
    ]
    candidates = [_chunk(str(i), 0.9) for i in range(10)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=3)

    assert len(result) == 3


@pytest.mark.asyncio
async def test_sliding_window_truncates():
    """긴 콘텐츠가 슬라이딩 윈도우로 잘린다."""
    from src.tools.internal.reranker_pipeline import rerank_3tier, SLIDING_WINDOW_SIZE

    reranker = AsyncMock()
    reranker.rerank.return_value = [{"index": 0, "score": 0.9}]

    long_content = "x" * (SLIDING_WINDOW_SIZE + 500)
    candidates = [{"chunk_id": "c1", "score": 0.9, "content": long_content}]
    await rerank_3tier(reranker, "질문", candidates, top_k=5)

    # reranker에 전달된 문서가 SLIDING_WINDOW_SIZE로 잘렸는지 확인
    call_args = reranker.rerank.call_args
    passed_doc = call_args[0][1][0]  # documents[0]
    assert len(passed_doc) == SLIDING_WINDOW_SIZE


@pytest.mark.asyncio
async def test_fused_score_in_output():
    """출력에 융합 점수가 포함된다."""
    from src.tools.internal.reranker_pipeline import rerank_3tier

    reranker = AsyncMock()
    reranker.rerank.return_value = [{"index": 0, "score": 0.8}]
    candidates = [_chunk("a", 0.6)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)

    expected_fused = 0.7 * 0.8 + 0.3 * 0.6  # 0.74
    assert result[0]["score"] == pytest.approx(expected_fused)
```

- [ ] **Step 2: 테스트 실행 -- 실패 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_reranker_pipeline.py -x -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: reranker_pipeline.py 구현**

```python
# src/tools/internal/reranker_pipeline.py
"""3-tier 리랭킹 + 벡터-리랭커 융합 스코어."""

from src.observability.logging import get_logger

logger = get_logger(__name__)

PREFERRED_MIN_SCORE = 0.5
FALLBACK_MIN_SCORE = 0.15
LAST_RESORT_COUNT = 3
RERANKER_WEIGHT = 0.7
VECTOR_SCORE_WEIGHT = 0.3
SLIDING_WINDOW_SIZE = 1500


async def rerank_3tier(
    reranker,
    query: str,
    candidates: list[dict],
    top_k: int,
) -> list[dict]:
    """3-tier 리랭킹 + 융합 스코어."""
    # 1. 슬라이딩 윈도우
    documents = [_sliding_window(c["content"]) for c in candidates]

    # 2. CrossEncoder 리랭킹
    reranked = await reranker.rerank(query, documents, top_k=len(candidates))

    # 3. 융합 스코어
    scored = []
    for item in reranked:
        orig = candidates[item["index"]]
        fused = (
            RERANKER_WEIGHT * item["score"]
            + VECTOR_SCORE_WEIGHT * orig["score"]
        )
        scored.append({"data": orig, "fused_score": fused})

    scored.sort(key=lambda x: x["fused_score"], reverse=True)

    # 4. 3-tier 필터링
    tier1 = [s for s in scored if s["fused_score"] >= PREFERRED_MIN_SCORE]
    if len(tier1) >= 1:
        results = tier1[:top_k]
    else:
        tier2 = [s for s in scored if s["fused_score"] >= FALLBACK_MIN_SCORE]
        if tier2:
            results = tier2[:top_k]
        else:
            results = scored[:LAST_RESORT_COUNT]

    logger.info(
        "rerank_3tier",
        input=len(candidates),
        tier1=len(tier1),
        output=len(results),
    )

    return [{**r["data"], "score": r["fused_score"]} for r in results]


def _sliding_window(text: str) -> str:
    """긴 텍스트를 윈도우 크기로 자른다."""
    if len(text) > SLIDING_WINDOW_SIZE:
        return text[:SLIDING_WINDOW_SIZE]
    return text
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_reranker_pipeline.py -x -v`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add src/tools/internal/reranker_pipeline.py tests/test_reranker_pipeline.py
git commit -m "feat: L4 리랭킹 파이프라인 — 3-tier 동적 필터 + 융합 스코어"
```

---

## Chunk 3: LLM 의존 레이어 (L1) + max_tokens 수정

### Task 5: Query Expander (L1)

**Files:**
- Create: `src/tools/internal/query_expander.py`
- Create: `tests/test_query_expander.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_query_expander.py
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
    assert len(result) == 3  # 원본 + 최대 2개


@pytest.mark.asyncio
async def test_fallback_on_error():
    """LLM 실패 시 원본만 반환."""
    from src.tools.internal.query_expander import expand_queries

    llm = AsyncMock()
    llm.generate_json.side_effect = Exception("LLM timeout")

    result = await expand_queries(llm, "원본 질문")
    assert result == ["원본 질문"]


@pytest.mark.asyncio
async def test_fallback_on_invalid_json():
    """LLM이 잘못된 형식 반환 시 원본만."""
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
    assert len(result) == 2  # 원본 + "유효한 변형"
```

- [ ] **Step 2: 테스트 실행 -- 실패 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_query_expander.py -x -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: query_expander.py 구현**

```python
# src/tools/internal/query_expander.py
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_query_expander.py -x -v`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add src/tools/internal/query_expander.py tests/test_query_expander.py
git commit -m "feat: L1 쿼리 확장 — LLM 멀티쿼리 생성"
```

---

### Task 6: max_tokens 수정

**Files:**
- Modify: `src/config.py:97` (port 바로 위)
- Modify: `src/infrastructure/providers/llm/http_llm.py:15-18`
- Modify: `src/infrastructure/providers/factory.py:70-79`
- Create: `tests/test_http_llm_max_tokens.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_http_llm_max_tokens.py
"""HttpLLMProvider max_tokens 전달 검증."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx


@pytest.mark.asyncio
async def test_generate_includes_max_tokens():
    """generate()가 request body에 max_tokens를 포함한다."""
    from src.infrastructure.providers.llm.http_llm import HttpLLMProvider

    provider = HttpLLMProvider("http://localhost:8080", max_tokens=4096)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "답변"}}],
    }

    with patch.object(provider._client, "post", return_value=mock_response) as mock_post:
        await provider.generate("질문")
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_default_max_tokens():
    """기본 max_tokens가 4096이다."""
    from src.infrastructure.providers.llm.http_llm import HttpLLMProvider

    provider = HttpLLMProvider("http://localhost:8080")
    assert provider._max_tokens == 4096
```

- [ ] **Step 2: 테스트 실행 -- 실패 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_http_llm_max_tokens.py -x -v`
Expected: `TypeError: HttpLLMProvider.__init__() got an unexpected keyword argument 'max_tokens'`

- [ ] **Step 3: config.py에 llm_max_tokens 추가**

`src/config.py:101` (port 바로 위)에 추가:

```python
    # LLM 응답 최대 토큰 (MLX 기본 512 방지)
    llm_max_tokens: int = 4096
```

- [ ] **Step 4: HttpLLMProvider에 max_tokens 추가**

`src/infrastructure/providers/llm/http_llm.py` 수정:

```python
class HttpLLMProvider(LLMProvider):
    def __init__(self, base_url: str, system_prefix: str = "", max_tokens: int = 4096):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)
        self._system_prefix = system_prefix
        self._max_tokens = max_tokens
```

`generate()` 메서드의 json body에 `"max_tokens"` 추가:

```python
    async def generate(self, prompt: str, system: str = "") -> str:
        system_msg = self._build_system(system)
        response = await self._client.post(
            f"{self._base_url}/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "max_tokens": self._max_tokens,
            },
        )
```

`generate_stream()` 메서드의 json body에도 동일하게 추가:

```python
    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        system_msg = self._build_system(system)
        async with self._client.stream(
            "POST",
            f"{self._base_url}/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "stream": True,
                "max_tokens": self._max_tokens,
            },
        ) as response:
```

- [ ] **Step 5: factory.py에서 max_tokens 전달**

`src/infrastructure/providers/factory.py:70-79` 수정 -- `_create_llm` 메서드에 `settings` 참조:

```python
    def _create_llm(self, server_url: str, local_model: str, label: str) -> LLMProvider:
        """LLM 프로바이더 생성 (router/main 공통 로직)."""
        if server_url:
            from .llm.http_llm import HttpLLMProvider

            logger.info("Using HTTP LLM server (%s): %s", label, server_url)
            return HttpLLMProvider(
                base_url=server_url,
                system_prefix=_LLM_SYSTEM_PREFIX,
                max_tokens=self._settings.llm_max_tokens,
            )
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_http_llm_max_tokens.py -x -v`
Expected: 2 passed

- [ ] **Step 7: 커밋**

```bash
git add src/config.py src/infrastructure/providers/llm/http_llm.py src/infrastructure/providers/factory.py tests/test_http_llm_max_tokens.py
git commit -m "fix: max_tokens 4096 명시 — MLX 기본 512 응답 잘림 해결"
```

---

## Chunk 4: 오케스트레이터 리팩터링 + 통합

### Task 7: rag_search.py 리팩터링 + bootstrap 수정

**Files:**
- Modify: `src/tools/internal/rag_search.py` (전체 리팩터링)
- Modify: `src/bootstrap.py:122-126` (router_llm 주입)
- Create: `tests/test_rag_search_pipeline.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_rag_search_pipeline.py
"""RAG 검색 파이프라인 통합 테스트."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.domain.models import SearchScope
from src.domain.agent_context import AgentContext


def _make_context() -> AgentContext:
    return AgentContext(session_id="s1", user_id="u1", user_role="EDITOR")


def _make_scope() -> SearchScope:
    return SearchScope(domain_codes=["INS"], security_level_max="INTERNAL")


def _mock_chunk(chunk_id: str, score: float) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "doc1",
        "content": f"content-{chunk_id}",
        "chunk_index": 0,
        "score": score,
        "file_name": "test.pdf",
        "title": "test",
    }


@pytest.mark.asyncio
async def test_full_pipeline_with_reranker():
    """전체 파이프라인: 확장 -> 검색 -> 노이즈 -> 이웃 -> 리랭킹 -> 가드."""
    from src.tools.internal.rag_search import RAGSearchTool

    embedder = AsyncMock()
    embedder.embed_batch.return_value = [[0.1] * 10, [0.2] * 10, [0.3] * 10]

    store = AsyncMock()
    store.hybrid_search.return_value = [
        _mock_chunk(f"c{i}", 0.9 - i * 0.05) for i in range(10)
    ]
    store.get_neighbor_chunks.return_value = []

    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": 0, "score": 0.9},
        {"index": 1, "score": 0.8},
        {"index": 2, "score": 0.7},
        {"index": 3, "score": 0.6},
        {"index": 4, "score": 0.5},
    ]

    router_llm = AsyncMock()
    router_llm.generate_json.return_value = ["변형1", "변형2"]

    tool = RAGSearchTool(
        embedding_provider=embedder,
        vector_store=store,
        reranker=reranker,
        router_llm=router_llm,
    )

    result = await tool.execute(
        {"query": "보험금 청구"},
        _make_context(),
        _make_scope(),
    )

    assert result.success is True
    assert len(result.data) > 0
    # embed_batch가 3개 쿼리로 호출됨
    embedder.embed_batch.assert_called_once()
    assert len(embedder.embed_batch.call_args[0][0]) == 3


@pytest.mark.asyncio
async def test_pipeline_without_reranker():
    """리랭커 없을 때 top_k 절단."""
    from src.tools.internal.rag_search import RAGSearchTool

    embedder = AsyncMock()
    embedder.embed_batch.return_value = [[0.1] * 10]

    store = AsyncMock()
    store.hybrid_search.return_value = [
        _mock_chunk(f"c{i}", 0.9 - i * 0.1) for i in range(10)
    ]
    store.get_neighbor_chunks.return_value = []

    router_llm = AsyncMock()
    router_llm.generate_json.side_effect = Exception("LLM down")

    tool = RAGSearchTool(
        embedding_provider=embedder,
        vector_store=store,
        reranker=None,
        router_llm=router_llm,
    )

    result = await tool.execute(
        {"query": "테스트"},
        _make_context(),
        _make_scope(),
    )

    assert result.success is True
    assert len(result.data) <= 5  # default_top_k


@pytest.mark.asyncio
async def test_empty_query():
    from src.tools.internal.rag_search import RAGSearchTool

    tool = RAGSearchTool(
        embedding_provider=AsyncMock(),
        vector_store=AsyncMock(),
        router_llm=AsyncMock(),
    )
    result = await tool.execute({"query": ""}, _make_context(), _make_scope())
    assert result.success is False
```

- [ ] **Step 2: 테스트 실행 -- 실패 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_rag_search_pipeline.py -x -v`
Expected: `TypeError: RAGSearchTool.__init__() got an unexpected keyword argument 'router_llm'`

- [ ] **Step 3: rag_search.py 리팩터링**

`src/tools/internal/rag_search.py` 전체 교체:

```python
"""RAG Search Tool: 5-layer 파이프라인 오케스트레이터.

L1 쿼리확장 -> 멀티쿼리 검색 -> L2 노이즈필터 -> L3 이웃확장 -> L4 리랭킹 -> L5 가드
"""

import time
from typing import Optional

from src.infrastructure.providers.base import EmbeddingProvider, LLMProvider, RerankerProvider
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import get_logger
from src.domain.models import SearchScope
from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.query_expander import expand_queries
from src.tools.internal.noise_filter import filter_noise
from src.tools.internal.neighbor_expander import expand_neighbors
from src.tools.internal.reranker_pipeline import rerank_3tier
from src.tools.internal.result_guard import guard_results

logger = get_logger(__name__)

CANDIDATE_POOL_SIZE = 50


class RAGSearchTool:
    """RAG 검색 도구 (ScopedTool). 5-layer 파이프라인."""

    name = "rag_search"
    description = "문서 벡터 검색 + 키워드 검색 하이브리드"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색 쿼리"},
        },
        "required": ["query"],
    }

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        reranker: Optional[RerankerProvider] = None,
        router_llm: Optional[LLMProvider] = None,
        default_top_k: int = 5,
    ):
        self._embedder = embedding_provider
        self._store = vector_store
        self._reranker = reranker
        self._router_llm = router_llm
        self._default_top_k = default_top_k

    async def execute(
        self,
        params: dict,
        context: AgentContext,
        scope: SearchScope,
    ) -> ToolResult:
        query = params.get("query", "")
        if not query:
            return ToolResult.fail("query is required")

        top_k = params.get("max_vector_chunks", self._default_top_k)
        t_start = time.time()

        # L1. 쿼리 확장
        if self._router_llm:
            queries = await expand_queries(self._router_llm, query)
        else:
            queries = [query]

        # 멀티쿼리 임베딩 (배치)
        embeddings = await self._embedder.embed_batch(queries)

        # 멀티쿼리 하이브리드 검색 + 합산
        candidates = await self._multi_query_search(
            queries, embeddings, scope,
        )

        if not candidates:
            return ToolResult.ok([], method="rag_search", chunks_found=0)

        # L2. 노이즈 필터
        candidates = filter_noise(candidates)

        # L3. 인접 청크 확장
        candidates = await expand_neighbors(self._store, candidates)

        # L4. 리랭킹
        if self._reranker and len(candidates) > top_k:
            results = await rerank_3tier(
                self._reranker, query, candidates, top_k,
            )
        else:
            results = candidates[:top_k]

        # L5. 결과 가드
        results = guard_results(results)

        total_ms = (time.time() - t_start) * 1000
        logger.info(
            "rag_pipeline_complete",
            queries=len(queries),
            candidates_before_filter=len(candidates),
            final=len(results),
            latency_ms=round(total_ms, 1),
        )

        return ToolResult.ok(
            results,
            method="rag_search",
            chunks_found=len(results),
        )

    async def _multi_query_search(
        self,
        queries: list[str],
        embeddings: list[list[float]],
        scope: SearchScope,
    ) -> list[dict]:
        """멀티쿼리 검색 후 합산. chunk_id 기준 최고 점수 유지."""
        domain_codes = scope.domain_codes if scope.domain_codes else None
        all_results: dict[str, dict] = {}

        for query, embedding in zip(queries, embeddings):
            results = await self._store.hybrid_search(
                embedding=embedding,
                text_query=query,
                limit=CANDIDATE_POOL_SIZE,
                domain_codes=domain_codes,
                allowed_doc_ids=scope.allowed_doc_ids,
                max_security_level=scope.security_level_max,
            )
            for r in results:
                cid = r["chunk_id"]
                if cid not in all_results or r["score"] > all_results[cid]["score"]:
                    all_results[cid] = r

        return sorted(
            all_results.values(),
            key=lambda x: x["score"],
            reverse=True,
        )
```

- [ ] **Step 4: bootstrap.py에 router_llm 주입**

`src/bootstrap.py:122-126` 수정:

```python
    # 6. Tool Registry
    tool_registry = ToolRegistry()
    tool_registry.register(RAGSearchTool(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        reranker=reranker,
        router_llm=router_llm,
    ))
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_rag_search_pipeline.py -x -v`
Expected: 3 passed

- [ ] **Step 6: 전체 회귀 테스트**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/ --ignore=tests/test_kms_client.py -x -v`
Expected: 전체 통과

- [ ] **Step 7: 커밋**

```bash
git add src/tools/internal/rag_search.py src/bootstrap.py tests/test_rag_search_pipeline.py
git commit -m "refactor: rag_search 5-layer 파이프라인 — 쿼리확장/노이즈/이웃/리랭킹/가드"
```

---

## Chunk 5: 배포 + 검증

### Task 8: Docker 빌드 + 실제 테스트

- [ ] **Step 1: Docker 빌드**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && docker compose build ai-platform`
Expected: 빌드 성공

- [ ] **Step 2: 컨테이너 재시작**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && docker compose up -d ai-platform`
Expected: 컨테이너 정상 기동

- [ ] **Step 3: 헬스체크**

Run: `curl -s http://localhost:8000/health | python -m json.tool`
Expected: `{"status": "ok"}`

- [ ] **Step 4: 실제 RAG 질의 테스트**

```bash
curl -s http://localhost:8000/api/chat \
  -H "Authorization: Bearer aip_dev_admin" \
  -H "Content-Type: application/json" \
  -d '{"question": "DB손해보험 운전자보험 보장내용 알려줘"}' | python -m json.tool
```

Expected:
- 응답이 잘리지 않음 (max_tokens 4096 적용)
- 검색 결과가 5개 이상 (멀티쿼리 + 이웃 확장)
- trace에서 쿼리 확장, 리랭킹 로그 확인

- [ ] **Step 5: ai-worker와 품질 비교**

동일 질문으로 ai-worker (KMS 챗봇)과 ai-platform을 비교:
- 문서 개수
- 답변 완성도
- 응답 잘림 여부

---

## 검증 요약

```bash
# 전체 테스트
cd /Users/eyjs/Desktop/WorkSpace/ai-platform
.venv/bin/python -m pytest tests/ --ignore=tests/test_kms_client.py -x -v

# 개별 레이어 테스트
.venv/bin/python -m pytest tests/test_noise_filter.py -v
.venv/bin/python -m pytest tests/test_result_guard.py -v
.venv/bin/python -m pytest tests/test_neighbor_expander.py -v
.venv/bin/python -m pytest tests/test_reranker_pipeline.py -v
.venv/bin/python -m pytest tests/test_query_expander.py -v
.venv/bin/python -m pytest tests/test_http_llm_max_tokens.py -v
.venv/bin/python -m pytest tests/test_rag_search_pipeline.py -v
```
