# 레이어 책임 계약 — KMS · ai-platform · docforge

> 3-서비스 사이의 데이터 소유권(SoT)과 동기화 경로를 한 장으로 못 박는다.
> 결정 배경: [ADR-009](../adr/adr-009-rag-single-source-of-truth.md) (G24, 2026-06-08).

## 책임 매트릭스

| 데이터/책임 | SoT (단일 진실원천) | 다른 서비스의 접근 방식 |
|---|---|---|
| **문서 원문 · 메타데이터 · 배치(placement) · 권한** | **KMS** (kms-pg + 스토리지) | ai-platform은 webhook payload로 수신만. 역질의 필요 시 KMS HTTP API |
| **RAG 데이터** (청크 · 임베딩 · FTS/trgm 인덱스) | **ai-platform** (aip-pg pgvector `document_chunks`) | KMS는 보유하지 않는다. 채팅/검색은 ai-platform HTTP/SSE 프록시 |
| **대화 세션 · 턴 · 인텐트** | **ai-platform** (aip-pg) | KMS `chat` 모듈은 얇은 프록시(provider 패턴) — 상태 없음 |
| **문서 파싱** (PDF→markdown, OCR, VLM) | **docforge** (stateless, 자체 SQLite 내구 큐만 보유 — ADR-007) | KMS·ai-platform 모두 HTTP 호출만. docforge는 어떤 PG에도 접속하지 않는다 |

## 동기화 = 단일 경로 (outbox → webhook)

```
KMS 쓰기 트랜잭션 ──▶ outbox_events (같은 $transaction, ADR-006)
                         │  디스패처 폴링 (네트워크 I/O는 트랜잭션 밖)
                         ▼
                  ai-platform /webhook (document.created/updated/…)
                         │  job_queue (PG SKIP LOCKED) → kms_sync
                         ▼
              parse(docforge) → chunk → embed → aip-pg UPSERT
                                        (external_id = KMS documentId, 멱등)
```

- **이 경로 외의 동기화 금지.** KMS가 aip-pg에 직접 쓰거나, ai-platform이 kms-pg의 RAG성 테이블을 만드는 것은 계약 위반.
- 멱등성 키는 `document_chunks.external_id` (부분 유니크 인덱스 `WHERE external_id IS NOT NULL`).
- 유실 방어: KMS측 outbox(PENDING 재시도) + ai-platform측 job_queue(재시도 3회) — 검증은 Step 17 E2E(`apps/api/tests/e2e/`).

## 금지 사항 (계약 위반 체크리스트)

1. ❌ KMS 스키마에 RAG성 테이블(청크/임베딩/벡터) 추가 — RAG SoT는 ai-platform. (과거 잔재는 `20260608000000_drop_dead_rag_schema`로 드롭됨)
2. ❌ ai-platform이 kms-pg에 직접 접속 — KMS 데이터는 webhook/HTTP로만.
3. ❌ KMS가 ai-platform 내부 레이어(벡터DB, 임베딩, LangGraph) 직접 참조 — HTTP(X-API-Key)/SSE만 (KMS ADR-044).
4. ❌ docforge에 PG 의존 추가 — standalone 도구, 내구 상태는 자체 SQLite만 (ADR-007).
5. ❌ outbox를 우회하는 KMS→ai-platform 알림 (fire-and-forget HTTP 등) — G20 회귀.

## 파일 형식 계약 (운영 사실)

- KMS 업로드 허용: `.pdf` `.md` `.csv` (multer fileFilter)
- ai-platform ingestion 허용: `pdf` `csv` `xlsx` `xls` (`ALLOWED_EXTENSIONS`, parsing/engine.py)
- **교집합 = `pdf` `csv`** — E2E·운영 문서 흐름은 이 안에서. `.md`는 KMS에 들어가도 RAG에 적재되지 않는다(현재 사양).
