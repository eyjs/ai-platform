# ADR-009: RAG 단일 진실원천 = ai-platform pgvector — KMS 죽은 RAG 스키마 드롭 (G24)

## Status
Accepted (2026-06-08)

## Context

3-서비스(ai-platform · KMS · docforge) 체계에서 **RAG 데이터가 어디에 있는가**에 대한 이중스토어 혼선이 있었다:

- ai-platform aip-pg(`:5434/ai_platform`)에 `document_chunks` — **실사용** (Knowledge Pipeline의 벡터+FTS+trgm 저장소)
- KMS kms-pg(`:5432/kms`)에도 **동명 테이블** `document_chunks` — 구 RAG 엔진(`packages/ai-worker`)의 잔재

KMS ai-worker는 ADR(KMS ADR-043/044)에 따라 ai-platform으로 재설계·흡수되었고 KMS `fae75cf8`에서 완전 제거되었다. 그러나 ai-worker가 raw SQL 마이그레이션으로 만든 테이블들은 kms-pg에 그대로 남았다 (2026-06-08 정찰 기준):

| 테이블 | 행수 | 최종 쓰기 | 비고 |
|---|---|---|---|
| `document_chunks` | 17,307 | 2026-03-06 | embedding vector(1024) + search_vector tsvector |
| `document_contents` | 1,023 | 2026-03-06 | 문서 파싱 결과 |
| `processing_jobs` | 2,727 | 2026-03-19 | 처리 작업 추적 |
| `chat_sessions` / `chat_messages` / `chat_turns` / `intent_examples` | 0 | — | 채팅/인텐트 잔재 |

7개 모두 **schema.prisma 모델 없음**, KMS main 코드(api/web/shared/mcp-kms) 읽기/쓰기 **0건** (테스트 포함 전수 grep + queryRaw 사용처 확인). 데이터는 ai-worker 제거 시점에 정지된 stale 상태.

문제: 죽은 동명 테이블이 살아있는 17K행을 품고 있으면, 스키마 자체가 "KMS에도 RAG 데이터가 있다"는 거짓 문서 역할을 한다. 신규 작업자가 어느 쪽이 진짜인지 확인하는 비용이 반복 발생한다 (P3 로드맵 "이중스토어 해소", G24).

## Decision

1. **RAG 단일 진실원천(SoT) = ai-platform pgvector (aip-pg `document_chunks`)** 로 확정한다.
2. **KMS 죽은 RAG 스키마는 드롭한다** — 택1 (A)정리 / (B)명시적 보류 중 **(A)**, 2026-06-08 사용자 확정.
   - KMS 마이그레이션 `20260608000000_drop_dead_rag_schema`: 테이블 7개 + 미사용 pgvector extension 드롭 (vector/tsvector 컬럼은 해당 테이블에만 존재했음을 확인 후).
   - 드롭 전 전체 백업: `~/Backups/kms-dead-rag-tables-20260608.sql.gz` (117MB).
3. **레이어 책임 계약을 명문화한다** — `docs/architecture/layer-responsibility.md` (KMS=원문/배치 SoT, ai-platform=RAG SoT, docforge=stateless 파싱, 동기화=outbox→webhook 단일경로).

## Consequences

### 검증 (2026-06-08, 라이브)
- KMS: `prisma migrate deploy` + `migrate status` 정합, `tsc --noEmit` 0에러, kms-api 헬스 green, 컨테이너 로그 클린.
- 불침범: `pg_trgm`(살아있는 trigram 검색)·`outbox_events`·`documents`(1,034행)·placements 등 전부 보존.
- **Step 17 E2E 풀 라이브 green (5 passed / 1 skipped)**: 골든패스 3건(업로드→outbox→webhook→ingestion→aip-pg 단일행/멱등성)이 드롭 후에도 라이브로 통과. G21/G23 계약 테스트 green. 유일한 skip은 G20 라이브 주입 게이트(설계상, Step 18에서 이미 라이브 실증).
- E2E 하니스 bitrot 3건을 함께 수리(라이브 골든패스를 실행 가능하게): 업로드 기본 확장자 `.txt`→`.csv`(KMS 허용 ∩ ai-platform ingestion = pdf/csv), KMS JWT `sub`에 실 user UUID 주입(`AIP_E2E_KMS_USER_ID`), 동기화 대기 30s→90s(job_queue 재시도 1사이클 흡수).

### 트레이드오프
- 17K행 드롭은 비가역 → pg_dump 백업으로 완화. 원문은 KMS `documents`+스토리지에, RAG본은 aip-pg에 이미 존재하므로 정보 손실 없음.
- 향후 KMS 자체 RAG 계획이 생기면 새 마이그레이션으로 재구축한다. 이 결정이 금지하는 것은 *죽은 스키마 방치*이지 미래 기능이 아니다.

### 관련
- KMS ADR-044(ai-worker 완전 제거), ADR-045(이 결정의 KMS측 기록)
- ai-platform ADR-006(Transactional Outbox, 동기화 단일경로의 내구성)
- 레이어 책임 계약: `docs/architecture/layer-responsibility.md`
