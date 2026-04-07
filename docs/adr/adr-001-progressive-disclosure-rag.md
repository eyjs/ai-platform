# ADR-001: Progressive Disclosure RAG — 3단계 공개 설계

## 상태
Accepted (2026-04-06)

## 컨텍스트

기존 `RAGSearchTool`은 매 요청마다 `hybrid_search()`로 문서 본문 전체를 로드했다.
질문의 성격에 따라 메타데이터만 필요한 경우(예: "어떤 문서가 있나요?")에도
content와 embedding 벡터를 포함한 전체 청크를 응답에 포함시켜 컨텍스트 윈도우를 불필요하게 소모했다.

Claude Code에서 도입된 단계적 공개(Progressive Disclosure) 패턴을 적용하여
요청 목적에 따라 반환 데이터의 범위를 조절하고자 했다.

## 결정 과정

### 옵션 1: metadata 전용 헬퍼 별도 작성 (거부)

`_build_metadata_vector_query`, `_fulltext_metadata_search`, `_trigram_metadata_search`,
`_rrf_merge_metadata` 등 새 메서드를 별도로 작성하는 방안.

거부 이유: 기존 헬퍼와 90% 중복된 코드가 발생했고, `VectorStore` 파일이 880줄로
프로젝트 최대 파일 크기 제한(800줄)을 초과했다.

### 옵션 2: MetadataSearchMixin 클래스 분리 (거부)

metadata 관련 메서드를 Mixin 클래스로 분리하는 방안.

거부 이유: 클래스 계층 복잡화. `VectorStore`가 이미 `AbstractVectorStore`를 구현하고 있는데
Mixin을 추가하면 다중 상속으로 인한 MRO 복잡도가 증가한다.

### 옵션 3: 기존 헬퍼 파라미터 확장 — Strangler Fig (채택)

기존 내부 헬퍼 `_build_vector_query`, `_fulltext_search`, `_trigram_search`에
`metadata_only: bool = False` 파라미터를 추가하여 SELECT 절을 분기한다.
`_rrf_merge_metadata`를 제거하고 `_rrf_merge`에 `row_converter` 콜백 파라미터를 추가한다.
기존 `hybrid_search()` 공개 시그니처는 변경하지 않는다.

채택 이유:
- 기존 공개 API 하위 호환 완전 유지
- 파일 크기 880줄 → 729줄 (제한 준수)
- 새 메서드 2개(`metadata_search`, `fetch_chunks_by_doc_ids`)만 공개 API에 추가

## 결정

`VectorStore`에 다음 변경을 적용한다:

**신규 공개 메서드 2개**
- `metadata_search(query, ...)`: content/embedding 컬럼을 SELECT에서 제외하는 경량 검색
- `fetch_chunks_by_doc_ids(doc_ids, ...)`: 특정 문서 ID로 본문 청크를 직접 조회

**기존 내부 헬퍼 파라미터 확장**
- `_build_vector_query(metadata_only=False)`: SELECT 절을 `_select_columns()` 헬퍼로 분기
- `_fulltext_search(metadata_only=False)`: 동일 패턴 적용
- `_trigram_search(metadata_only=False)`: 동일 패턴 적용
- `_rrf_merge(row_converter=None)`: 기본값은 기존 `_row_to_dict`, metadata 모드는 다른 변환기 주입

**RAGSearchTool disclosure_level 3단계**
- Level 1: `metadata_search()` 호출 — 문서 ID, 제목, 도메인, 점수만 반환
- Level 2: `hybrid_search()` 호출 — 본문 포함 전체 반환 (기존 동작)
- Level 3: Level 1 후 LLM 판단으로 필요한 문서만 `fetch_chunks_by_doc_ids()` 호출

Profile YAML에서 `disclosure_level` 필드로 제어한다.

## 결과

- 기존 524개 테스트 전부 통과 (회귀 없음)
- 파일 크기 880줄 → 729줄 (800줄 제한 준수)
- Level 1 검색 시 content/embedding 제외로 응답 크기 약 60% 감소 예상
- 코드 리뷰 1차 FAIL(파일 크기 초과) → 리팩토링 후 2차 PASS

## 관련 파일

- `src/infrastructure/vector_store.py`
- `src/tools/internal/rag_search.py`
- `alembic/versions/009_*.py`
