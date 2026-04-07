# Changelog

모든 주요 변경 사항을 이 파일에 기록한다.
형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/)를 따른다.

---

## [Unreleased] — 2026-04-06

### Added — 웹 애플리케이션 신규 구축

- **모노레포 (Turborepo + pnpm)**: `web/` 하위에 Next.js 15 앱, NestJS BFF, 공유 디자인 시스템 패키지 구성
- **디자인 시스템**: CSS 변수 기반 디자인 토큰 + Tailwind CSS v4 `@theme` 연동. Button, Input, TextArea, Card, Badge, Modal, Toast, Dropdown, Tabs, Toggle, DataTable, StatCard, Skeleton, Avatar 15개 컴포넌트
- **NestJS BFF 인증**: JWT Access Token (15분) + Refresh Token (7일), HS256 (FastAPI 호환). `web_users` 테이블 마이그레이션. Roles Guard (ADMIN 역할 체크)
- **챗봇 멀티세션 UI**: GPT 스타일 사이드바 (날짜 그룹, 세션 목록). localStorage 기반 세션 영속 (최대 100개). SSE 스트리밍 4종 이벤트 처리 (token/replace/trace/done). 마크다운 렌더링 (코드 블록, 테이블, 인용문). 자동 스크롤 + FAB
- **Profile YAML 편집기**: Monaco Editor (dynamic import, 다크 테마). 2-panel 레이아웃 (에디터 60% + 미리보기/테스트 40%). 실시간 유효성 검증 (300ms 디바운스). 히스토리 패널 (슬라이드 인). Ctrl+S 저장 + 이탈 경고
- **NestJS Profile CRUD API**: 10개 엔드포인트 (CRUD + activate/deactivate + history + restore + tools). `profile_history` 테이블 자동 히스토리 기록
- **Profile 목록 화면**: 카드 그리드 (auto-fill, minmax 320px). 검색 디바운스, Mode/상태 필터. 활성화 토글, 삭제 확인 모달
- **통합 대시보드**: StatCard 4개 (30초 polling). Profile별 사용량 바 차트. 대화 로그 DataTable (페이지네이션)
- **NestJS 대시보드 API**: summary/usage/latency/logs 4개 집계 엔드포인트
- **인증 연동**: Next.js 미들웨어 (경로 보호), AuthProvider (Context), 13분 주기 자동 갱신. 관리자 레이아웃 + 사이드바 (접힘/펼침)

### Added — 백엔드 고도화

- **Progressive Disclosure RAG**: `VectorStore.metadata_search()`, `fetch_chunks_by_doc_ids()` 신규 추가. `RAGSearchTool.disclosure_level` 3단계 (1=메타데이터, 2=본문, 3=참조)
- **에이전트 메모리 3-스코프**: `AgentProfile.memory_scopes` (local/user/project). `tenant_memory`, `project_memory` 테이블. `ScopedMemoryLoader` 병렬 조회
- **파이프라인 파일 레벨 락**: `src/pipeline/lock.py` — O_CREAT|O_EXCL 원자적 락, `os.replace()` 원자적 쓰기
- **AgentExecutionPath Enum**: subagent/fork/team 3종 실행 경로 타입 선제 정의
- **검증 넛지 패턴**: `AgentProfile.validation_nudge_*` 필드 추가
- **Alembic 009 마이그레이션**: `tenant_memory`, `project_memory` 테이블 (UUID PK, JSONB, UNIQUE 제약)

### Changed — 백엔드

- `VectorStore._build_vector_query()`: `metadata_only` 파라미터 추가 (기본값 False, 하위 호환)
- `VectorStore._fulltext_search()`: `metadata_only` 파라미터 추가
- `VectorStore._trigram_search()`: `metadata_only` 파라미터 추가
- `VectorStore._rrf_merge()`: `row_converter` 콜백 파라미터 추가 (기본값 None → `_row_to_dict`)
- `RAGSearchTool.execute()`: disclosure_level 분기 구조로 리팩토링. 파일 880줄 → 729줄

### Fixed — 이전 세션

- 라우터/오케스트레이터 LLM 포트 8106 → 8105 수정
- general-chat 프로필 하드코딩 제거
- Anthropic locale 처리 수정
- async 모델 감지 오류 수정

---

## [0.3.0] — 2026-03-27

### Added

- 아키텍처 고도화: 로케일 시스템, 임베딩 라우터, thinking 분리
- fortune-saju 프로필 사주아치 전용 강화
- 사용자군 기반 Profile 접근 제어 (AccessPolicy)

---

## [0.2.0] — 2026-03-12

### Planned (미구현)

- Dual-Mode Execution Engine (LangGraph Foundation) — `docs/plans/2026-03-12-dual-mode-engine.md` 참조
