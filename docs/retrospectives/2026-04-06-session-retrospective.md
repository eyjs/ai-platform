# 세션 회고 — 2026-04-06

## 세션 개요

| 항목 | 내용 |
|------|------|
| 날짜 | 2026-04-06 |
| 파이프라인 실행 횟수 | 2회 (백엔드 고도화 + 웹앱 신규 구축) |
| 총 커밋 | 2건 (155파일, +18,800줄) |
| 총 테스트 | 524개 (백엔드, 웹앱 단위 테스트 미작성) |

---

## 주요 성과

### 작업 1: 백엔드 고도화 — Claude Code 패턴 적용

커밋: `feat: Progressive Disclosure RAG, 메모리 3-스코프, 파이프라인 락, 파싱 엔진`

| 구현 항목 | 수치 |
|-----------|------|
| Progressive Disclosure RAG (3-레벨) | VectorStore 2개 메서드 신규, 3개 기존 메서드 파라미터 확장 |
| 에이전트 메모리 3-스코프 | tenant_memory + project_memory 테이블 + ScopedMemoryLoader |
| 파이프라인 파일 레벨 락 | O_CREAT|O_EXCL 원자적 락 + status.json 원자적 쓰기 |
| AgentExecutionPath Enum | subagent/fork/team 3종 타입 선제 정의 |
| Alembic 009 마이그레이션 | UUID PK + JSONB + UNIQUE 제약 2개 테이블 |
| 코드 리뷰 결과 | 1차 FAIL (파일 880줄, 800줄 제한 초과) → 리팩토링 후 2차 PASS (729줄) |
| 테스트 결과 | 524 passed, 0 failed |

핵심 성과: VectorStore 파일 크기를 880줄에서 729줄로 17% 감소시키면서 기능을 확장했다.
중복 헬퍼 3개 제거 및 `row_converter` 콜백 패턴 도입으로 확장성과 유지보수성을 동시에 확보했다.

### 작업 2: 글로벌 설정 완비 — 디자인 에이전트 추가

이번 세션 중 수행된 글로벌 Claude 설정 감사 및 보완 작업:

| 구분 | 이전 | 이후 |
|------|------|------|
| 에이전트 수 | 11개 | 12개 |
| 스킬 수 | 4개 | 5개 |
| 룰 수 | 6개 | 8개 |
| 커맨드 수 | 4개 | 6개 |

추가된 항목:
- 디자인 에이전트 (`~/.claude/agents/designer.md`)
- 디자인 관련 스킬, 룰, 커맨드 4개
- 템플릿 4개 (screen-spec, component-spec, design-tokens, design-system)

방법론 문서 기준 37개 설정 항목 중 기존 30개 존재, 7개 누락 확인 후 보완 완료.

### 작업 3: AI Platform 웹앱 신규 구축

커밋: `feat(web): AI Platform 웹앱 — 채팅 UI + Profile 편집기 + 대시보드`

파이프라인 ID: `ai-platform-web-app-20260406`

**디자인 단계**
- 디자인 스펙 16개 파일, 2,639줄 생성
- 5개 화면 스펙 + 7개 컴포넌트 스펙 + 디자인 토큰 + 시스템 문서
- Stitch MCP를 통한 Figma 연동 시도 → Google OAuth DCR 미지원으로 인한 실패, 로컬 Markdown 스펙으로 대체

**구현 단계 (10개 태스크, 4단계)**

| 라운드 | 태스크 | 내용 |
|--------|--------|------|
| A | Task-001 | Turborepo + pnpm 모노레포, Next.js 15 + NestJS 스캐폴딩 |
| B (병렬) | Task-002 | 디자인 시스템 — 15개 UI 컴포넌트 (Button부터 DataTable까지) |
| B (병렬) | Task-003 | NestJS BFF — JWT 인증 (Access 15분 + Refresh 7일), web_users 테이블 |
| C (병렬) | Task-004 | 챗봇 멀티세션 — GPT 스타일 사이드바, SSE 스트리밍 4종 이벤트, 마크다운 렌더링 |
| C (병렬) | Task-006 | NestJS Profile CRUD — 10개 엔드포인트, profile_history 자동 기록 |
| C (병렬) | Task-009 | NestJS 대시보드 API — 4개 집계 쿼리 |
| C (병렬) | Task-010 | Next.js 인증 연동 — 미들웨어, AuthContext, 13분 주기 자동 갱신 |
| D (병렬) | Task-005 | Profile YAML 편집기 — Monaco Editor, 실시간 검증, 히스토리 패널 |
| D (병렬) | Task-007 | Profile 목록 — 카드 그리드, 검색/필터, 활성화 토글 |
| D (병렬) | Task-008 | 통합 대시보드 — StatCard 4개, 바 차트, 대화 로그 DataTable |

**최종 산출물**

| 지표 | 수치 |
|------|------|
| 생성 파일 수 | 120개 |
| 추가 코드 줄 수 | +15,097줄 |
| Next.js 라우트 | 8개 (static 6, dynamic 2) |
| First Load JS | 102kB (shared) |
| TypeScript 오류 | 0개 |
| Next.js 빌드 | SUCCESS |

---

## 기술 결정 기록

### TD-1: 하이브리드 아키텍처 (채팅 SSE 직접 / CRUD는 BFF 경유)

채팅 SSE 스트리밍은 Next.js 클라이언트에서 FastAPI를 직접 호출하고,
Profile CRUD와 대시보드 집계는 NestJS BFF를 경유하도록 분리했다.

근거: SSE를 BFF로 프록시할 경우 지연이 증가하고 불필요한 복잡도가 생긴다.
반면 CRUD는 히스토리 기록, 검증, 감사 로그 등 BFF 레이어가 필요하다.
두 요구사항이 상충하므로 역할에 따라 경로를 분리하는 것이 최적이었다.

### TD-2: Progressive Disclosure — Strangler Fig 패턴

기존 `hybrid_search()` 시그니처를 변경하지 않고, 새 메서드를 추가하고
기존 내부 헬퍼에 `metadata_only` 파라미터를 확장하는 Strangler Fig 패턴을 채택했다.

대안으로 metadata 전용 헬퍼를 별도 작성하는 방법은 중복 코드로 800줄 제한을 초과했고,
MetadataSearchMixin 클래스 분리는 계층 복잡화를 유발했다.
기존 코드를 유지하면서 새 동작을 추가하는 Strangler Fig가 하위 호환과 코드 크기 제약을 동시에 만족시켰다.

### TD-3: Monaco Editor 채택 (CodeMirror 6 대비)

YAML 편집기 엔진으로 Monaco Editor를 채택했다. VS Code와 동일한 엔진이라 YAML 지원이
우수하고, 자동완성 API가 풍부하다. 번들 크기는 dynamic import로 완화했다.

### TD-4: Turborepo 모노레포

Next.js 앱과 NestJS BFF를 같은 저장소에서 관리하고 디자인 토큰 CSS를 공유 패키지로 분리했다.
pnpm workspace와 Turborepo 파이프라인 캐시로 빌드 효율을 확보했다.

### TD-5: 대시보드 차트 — CSS 바 차트 (Recharts 계획 대비)

Recharts 번들을 절약하고 빠른 구현을 위해 CSS 기반 바 차트를 선택했다.
향후 복잡한 시각화가 필요해지면 Recharts로 교체 가능한 컴포넌트 경계를 유지했다.

---

## 이슈 및 교훈

### 이슈 1: 코드 리뷰 1차 FAIL — VectorStore 파일 크기 초과

- 상황: RAGSearchTool 확장 구현 후 VectorStore 파일이 880줄(제한 800줄)로 검토 실패
- 원인: metadata 전용 헬퍼를 중복 작성하면서 파일이 비대해짐
- 해결: `_build_vector_query`, `_fulltext_search`, `_trigram_search`에 `metadata_only` 파라미터를 추가하고, 별도였던 `_rrf_merge_metadata`를 `row_converter` 콜백으로 통합 → 729줄로 감소
- 교훈: 새 기능 추가 시 중복 코드를 작성하기보다 기존 코드를 파라미터로 확장하는 것이 파일 크기와 응집성 모두에서 유리하다.

### 이슈 2: Stitch MCP Google OAuth DCR 미지원

- 상황: 디자인 시스템 생성에 Stitch MCP(Figma 연동)를 사용하려 했으나 Google OAuth Dynamic Client Registration 미지원으로 인증 실패
- 해결: Markdown 기반 디자인 스펙 파일로 대체하여 동일한 수준의 사양을 확정
- 영향: 추가 소요 시간 없음 (Markdown 스펙이 오히려 버전 관리에 유리)
- 다음 단계: Anthropic Stitch 플러그인 업데이트 대기. 업데이트 후 기존 Markdown 스펙을 Figma로 내보낼 수 있음

### 이슈 3: 백그라운드 curl 샌드박스 제약

- 상황: 파이프라인 실행 중 백그라운드 curl 명령으로 외부 서비스 상태를 확인하려 했으나 샌드박스 환경에서 블로킹
- 해결: 네트워크 의존성을 제거하고 로컬 파일 기반 검증으로 대체
- 교훈: 파이프라인 에이전트는 외부 네트워크 의존을 최소화해야 한다. 검증은 로컬 빌드/타입체크로 충분하다.

### 이슈 4: 남은 이슈 (LOW/MEDIUM)

| # | 이슈 | 심각도 |
|---|------|--------|
| 1 | 레이턴시 차트 데이터 없음 (로그 테이블 미생성) | LOW |
| 2 | Profile 편집기 인라인 테스트 챗봇 미구현 | LOW |
| 3 | ESLint 설정 미완성 | LOW |
| 4 | BFF 마이그레이션 자동 실행 미설정 | MEDIUM |
| 5 | FastAPI CORS에 웹앱 Origin 미추가 | MEDIUM |

---

## 파이프라인 효율 통계

### 파이프라인 1: 백엔드 고도화

| 항목 | 수치 |
|------|------|
| 태스크 수 | 5개 (RAG, 메모리, 락, Enum, 마이그레이션) |
| 코드 리뷰 루프 | 2회 (1차 FAIL → 수정 → 2차 PASS) |
| 피드백 루프 | 0회 |
| 테스트 결과 | 524 passed, 0 failed |

### 파이프라인 2: 웹앱 신규 구축

| 항목 | 수치 |
|------|------|
| 태스크 수 | 10개 (Round 1~3, 4단계) |
| Plan Review | 1회 PASS (재작성 없음) |
| 코드 리뷰 루프 | 각 태스크 1회 PASS |
| 피드백 루프 | 0회 |
| 빌드 결과 | Next.js SUCCESS (8 routes, 102kB) |
| TypeScript | 0 errors |

---

## 다음 단계

| 우선순위 | 항목 | 비고 |
|----------|------|------|
| P0 | FastAPI CORS 설정 — `localhost:3000` 추가 | 웹앱 연동 직전 필수 |
| P0 | BFF TypeORM 마이그레이션 실행 스크립트 설정 | 배포 전 필수 |
| P1 | 웹앱 단위 테스트 작성 | 주요 훅/컴포넌트 대상 |
| P1 | Profile 편집기 인라인 테스트 챗봇 구현 | 요구사항 P1 항목 |
| P2 | Stitch MCP 인증 해결 후 Figma 내보내기 | Anthropic 플러그인 업데이트 대기 |
| P2 | v2.0 Self-Refine 그래프 구현 (LangGraph) | 이전 로드맵 항목 |
| P2 | 레이턴시 로그 테이블 추가 | 대시보드 완성도 향상 |
