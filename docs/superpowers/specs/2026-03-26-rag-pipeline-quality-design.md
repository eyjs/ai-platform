# RAG Pipeline 품질 강화 설계

**날짜**: 2026-03-26
**목표**: ai-platform RAG 검색 품질을 ai-worker 수준 이상으로 끌어올린다
**접근법**: rag_search.py Tool 내부에 독립 레이어 모듈을 점진 통합 (Approach A)

---

## 배경

ai-platform과 ai-worker의 RAG 품질 차이 원인 분석 결과:

| 항목 | ai-worker | ai-platform (현재) |
|------|-----------|-------------------|
| 후보 풀 | 50개 | top_k * 3 = 15개 |
| 쿼리 확장 | LLM 멀티쿼리 (3개) | 없음 |
| 노이즈 필터 | Score Gap (상대 30%) | 없음 |
| 인접 청크 | 상위 5개 앞뒤 확장 | 없음 |
| 리랭킹 | 3-tier + 융합 스코어 | top_k 절단만 |
| 콘텐츠 가드 | 없음 | 없음 |
| max_tokens | 명시적 설정 | 미설정 (MLX 기본 512) |

---

## 아키텍처

### 모듈 구조

```
src/tools/internal/
  rag_search.py            # 오케스트레이션 (조립만, 비즈니스 로직 없음)
  query_expander.py        # L1. 쿼리 확장
  noise_filter.py          # L2. 노이즈 필터
  neighbor_expander.py     # L3. 인접 청크 확장
  reranker_pipeline.py     # L4. 리랭킹 + 융합
  result_guard.py          # L5. 결과 가드

src/config.py              # max_tokens 환경변수
src/infrastructure/providers/llm/http_llm.py  # max_tokens 전달
```

### 설계 원칙

- **독립 레이어**: 각 모듈은 `list[dict] -> list[dict]` 시그니처 (L1만 `str -> list[str]`)
- **파사드 패턴**: 외부는 한 함수만 호출 (filter_noise, guard_results 등)
- **내부 전략 체이닝**: 레이어 안에서 자유롭게 전략 추가/교체 가능
- **결합도 최소화**: 레이어 내부를 고도화해도 파이프라인 동작에 영향 없음
- **권한 필터링**: SearchScope -> VectorStore SQL WHERE 절 (기존 유지, 파이프라인 밖)

### 데이터 플로우

```
질문
  |
  v
L1. query_expander.expand_queries(router_llm, query)
  |  -> [원본, 변형1, 변형2]
  v
    embed_batch(queries) -> embeddings
  |
  v
    multi_query_search(각 50 candidates) -> 합산/중복제거
  |
  v
L2. noise_filter.filter_noise(candidates)
  |  -> Score Gap 30% 이상 하락 시 절단 (최소 5개 유지)
  v
L3. neighbor_expander.expand_neighbors(store, candidates)
  |  -> 상위 5개 청크의 앞뒤 인접 청크 추가 (0.8 감쇠)
  v
L4. reranker_pipeline.rerank_3tier(reranker, query, candidates, top_k)
  |  -> CrossEncoder -> 융합 스코어(70% reranker + 30% vector)
  |  -> 3-tier: 0.5 이상 -> 0.15 이상 -> 최소 3개
  v
L5. result_guard.guard_results(results)
  |  -> PII 마스킹 (주민번호, 전화번호, 계좌번호)
  v
ToolResult.ok(results)
```

---

## 모듈 상세

### L1. query_expander.py

**역할**: LLM으로 원본 쿼리를 2개 변형 쿼리로 확장. 검색 재현율(recall) 향상.

**핵심 결정**:
- 변형 2개 (총 3쿼리) -- 3개 이상은 latency 대비 효과 미미
- Router LLM 사용 (경량 모델) -- Main LLM 낭비 방지
- 실패 시 원본만 사용 -- graceful degradation
- LRU 캐시 보류 -- 1차에서 복잡도 추가 불필요

**인터페이스**: `expand_queries(llm, query: str) -> list[str]`

**상수**:
- MAX_VARIANTS = 2

**향후 진화**: probe 신호 기반 확장 스킵 (고품질 시 bypass), 캐시 도입

### L2. noise_filter.py

**역할**: 검색 결과에서 저품질 청크를 사전 제거. 리랭커에 노이즈가 유입되는 것을 방지.

**핵심 전략**: Relative Score Gap Filtering
- 절대 임계값이 아닌 1등 점수 대비 상대적 하락률로 판단
- RRF 점수 범위(0.008~0.016)가 가변적이므로 절대값 기준 무의미

**인터페이스**: `filter_noise(candidates: list[dict]) -> list[dict]`

**상수**:
- RELATIVE_GAP_RATIO = 0.3 (30% 이상 하락 시 절단)
- MIN_KEEP_COUNT = 5 (최소 유지 개수)

**향후 진화**: outlier 필터, 중복 콘텐츠 필터 (전략 체이닝)

### L3. neighbor_expander.py

**역할**: 상위 청크의 앞뒤 인접 청크를 가져와 맥락 보강.

**인터페이스**: `expand_neighbors(vector_store, candidates: list[dict]) -> list[dict]`

**상수**:
- NEIGHBOR_EXPAND_TOP_N = 5
- NEIGHBOR_SCORE_DECAY = 0.8

**의존**: VectorStore.get_neighbor_chunks() (기존 메서드)

**향후 진화**: window 크기 조정, 문서 경계 처리, decay 전략 다양화

### L4. reranker_pipeline.py

**역할**: CrossEncoder 리랭킹 + 벡터-리랭커 융합 스코어 + 3-tier 동적 필터링.

**인터페이스**: `rerank_3tier(reranker, query: str, candidates: list[dict], top_k: int) -> list[dict]`

**상수**:
- PREFERRED_MIN_SCORE = 0.5 (1차: 고품질)
- FALLBACK_MIN_SCORE = 0.15 (2차: 최소 관련성)
- LAST_RESORT_COUNT = 3 (3차: 최소 보장)
- RERANKER_WEIGHT = 0.7
- VECTOR_SCORE_WEIGHT = 0.3
- SLIDING_WINDOW_SIZE = 1500

**처리 순서**:
1. 슬라이딩 윈도우로 긴 청크 자르기 (CrossEncoder 입력 제한)
2. CrossEncoder 리랭킹
3. 융합 스코어 = 0.7 * reranker + 0.3 * vector
4. 3-tier 필터링

**ai-worker 대비 1차 보류 항목**:
- prior_doc_boost (이전 대화 문서 가산점) -- 멀티프로필 환경에서 세션 컨텍스트 설계 필요
- 문서 단위 승격 (top-2 평균) -- 2차에서 검토
- 비교 질문 균형 조정 -- 2차에서 검토

### L5. result_guard.py

**역할**: LLM에 전달하기 전 검색 결과의 민감 콘텐츠 필터링/마스킹.

**인터페이스**: `guard_results(candidates: list[dict]) -> list[dict]`

**1차 전략**: PII 패턴 마스킹
- 주민번호: `\d{6}-[1-4]\d{6}` -> [주민번호]
- 전화번호: `01[016789]-\d{3,4}-\d{4}` -> [전화번호]
- 계좌번호: `\d{3,6}-\d{2,6}-\d{2,6}` -> [계좌번호]

**향후 진화**: NER 기반 마스킹, 비속어 필터, 컴플라이언스 가드 (전략 체이닝)

**권한 필터링과의 구분**:
| 관심사 | 담당 | 위치 |
|--------|------|------|
| 문서 접근 권한 | SearchScope -> VectorStore SQL | 인프라 (검색 전) |
| 콘텐츠 민감정보 | result_guard.py | RAG 파이프라인 L5 (검색 후) |

---

## max_tokens 수정

**문제**: MLX 서버가 max_tokens 미지정 시 기본 512로 제한 -> 응답 잘림 (finish_reason: length)

**해결**:
1. `src/config.py`: `llm_max_tokens: int = 4096` 환경변수 추가
2. `HttpLLMProvider.__init__()`: `max_tokens` 파라미터 추가
3. `generate()`, `generate_stream()`: request body에 `"max_tokens"` 포함
4. `bootstrap.py`: Settings.llm_max_tokens -> HttpLLMProvider에 전달

---

## rag_search.py 변경

### 생성자 변경
- `router_llm: LLMProvider` 파라미터 추가 (쿼리 확장용)
- `default_top_k: int = 5` 유지

### execute() 파이프라인
```python
async def execute(self, params, context, scope) -> ToolResult:
    query = params["query"]
    top_k = params.get("max_vector_chunks", self._default_top_k)

    # L1. 쿼리 확장
    queries = await expand_queries(self._router_llm, query)

    # L2-prep. 멀티쿼리 검색 + 합산
    candidates = await self._multi_query_search(queries, scope)

    # L2. 노이즈 필터
    candidates = filter_noise(candidates)

    # L3. 인접 청크 확장
    candidates = await expand_neighbors(self._store, candidates)

    # L4. 리랭킹
    if self._reranker and len(candidates) > top_k:
        results = await rerank_3tier(self._reranker, query, candidates, top_k)
    else:
        results = candidates[:top_k]

    # L5. 결과 가드
    results = guard_results(results)

    return ToolResult.ok(results, method="rag_search", chunks_found=len(results))
```

### _multi_query_search() (private 메서드)
- 각 쿼리에 대해 embed + hybrid_search(limit=50)
- 결과 합산: chunk_id 기준 중복 제거, 최고 점수 유지
- 점수 내림차순 정렬 반환

---

## 테스트 전략

| 모듈 | 테스트 | 방법 |
|------|--------|------|
| query_expander | LLM mock, 변형 반환 검증, 실패 시 원본만 | unit |
| noise_filter | gap 경계값, 최소 유지, 빈 입력, 동점 | unit |
| neighbor_expander | VectorStore mock, 중복 방지, decay 검증 | unit |
| reranker_pipeline | 3-tier 경계값, 융합 계산, 빈 입력 | unit |
| result_guard | PII 패턴 마스킹, 비매칭 통과 | unit |
| rag_search | 전체 파이프라인 (mock 주입) | integration |
| http_llm | max_tokens 전달 검증 | unit |
| E2E | Docker 배포 후 KMS 문서 질의 품질 비교 | manual |

---

## 보류 항목 (2차)

| # | 항목 | 이유 |
|---|------|------|
| 1 | prior_doc_boost | 멀티프로필 세션 컨텍스트 설계 필요 |
| 2 | probe 신호 기반 확장 스킵 | 1차에서 효과 측정 후 판단 |
| 3 | LRU 캐시 (쿼리 확장) | 1차 복잡도 최소화 |
| 4 | 비교 질문 균형 조정 | QuestionType 연동 필요 |
| 5 | 문서 단위 승격 | 효과 측정 후 판단 |
| 6 | NER 기반 PII 마스킹 | 패턴 기반으로 충분한지 검증 후 |
