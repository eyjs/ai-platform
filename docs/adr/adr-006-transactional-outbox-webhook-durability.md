# ADR-006: Transactional Outbox로 KMS→ai-platform 문서 동기화 발행 내구화 (G20)

## Status
Accepted (2026-06-05)

## Context

KMS(문서관리)는 문서가 생성/수정/배치/라이프사이클 전환/삭제될 때 ai-platform(RAG)에 webhook으로 알려 벡터 인덱스를 (재)동기화한다. 기존 발행은 **fire-and-forget**이었다:

- `placements.service.ts`의 `dispatchPlacementSync(...)`가 비즈니스 트랜잭션 **밖**에서 `webhooksService.dispatch(...)`를 await/.catch 없이 호출
- `documents.service.ts`의 create/update/lifecycle/delete/file_uploaded도 DB 쓰기 후 트랜잭션 밖에서 `dispatch(...)` 호출

이 구조의 결함(P3 Step 17 실패주입①, G20으로 가시화):

1. **커밋成·발행失의 조용한 어긋남**: 배치/문서 변경은 KMS에 커밋됐는데 webhook 수신부가 5xx를 반환하거나 네트워크가 끊기면, 단기 재시도(`sendWebhook` MAX_RETRIES=3) 소진 후 이벤트가 **유실**된다. KMS DB와 ai-platform RAG가 영구히 어긋나도 아무도 모른다.
2. **프로세스 강제종료 취약**: 커밋 직후 발행 전에 KMS 프로세스가 죽으면 그 이벤트는 메모리에만 있던 호출이라 영영 발송되지 않는다.
3. **내구적 재시도 부재**: 단기 in-process 재시도만 있고, 장기(분/시간 단위) 내구 재시도가 없다.

Step 17b는 이를 `test_g20_webhook_fire_and_forget`(`@pytest.mark.xfail(strict=True)`)으로 못 박아두었다 — "현재 코드는 실패해야 정상, Step 18 Outbox에서 green 전환".

### 제약
- **PostgreSQL 단일 스택** (루트 CLAUDE.md 절대 규칙): Redis, Kafka, RabbitMQ 등 별도 메시지 브로커/큐 인프라 도입 금지.
- 외부 구독자가 받는 webhook 페이로드(event명 + data 필드)는 **기존과 동일**해야 한다(수신측 무변).
- KMS는 ai-platform 내부를 바라보지 않는다(webhook URL로 도달만).

## Decision

**Transactional Outbox 패턴을 PostgreSQL 테이블 + 폴링 디스패처로 구현한다.**

### 발행 (트랜잭션 내부)
- 신규 `outbox_events` 테이블 (`id`, `aggregate_id`, `event_type`, `payload`(jsonb), `status`(PENDING|SENT|FAILED), `attempts`, `last_error`, `created_at`, `updated_at`, `sent_at`, `@@index([status, created_at])`).
- `OutboxService.enqueue(tx, ...)`가 **비즈니스 트랜잭션과 같은 `$transaction` 콜백 안**에서 PENDING 이벤트를 insert한다. 그 결과 "문서/배치 변경 + 이력 + 이벤트 발행"이 **하나의 커밋**으로 원자화된다. 커밋되면 이벤트도 반드시 존재하고, 롤백되면 이벤트도 없다.
- placements.create/bulkCreate는 이미 `$transaction`을 보유 → enqueue만 추가. documents의 create/update/lifecycle/softDelete는 기존에 트랜잭션이 없던 write+history를 `$transaction`으로 감싸고 그 안에 enqueue. attachFile(file_uploaded)은 기존 tx를 재사용.

### 디스패처 (폴링)
- `OutboxService.dispatchPending()`이 `SELECT ... WHERE status='PENDING' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT N`으로 이벤트를 클레임(다중 인스턴스/워커 안전)한 뒤, **기존 webhook 발송 로직을 재사용**(`WebhooksService.deliverForOutbox`)하여 전달한다.
  - 성공 → `SENT`, `sent_at=now`.
  - 실패 → `attempts++`, `last_error` 기록, `PENDING` 유지(다음 폴에서 재시도).
  - `attempts`가 MAX(기본 10) 도달 → `FAILED`(무한 재시도 방지 + 운영 가시화).
  - **활성 구독자 0건 = "보낼 곳 없음 = 성공"으로 SENT** 처리(dev에 구독자 없을 때 무한 PENDING 적체 방지).
- `OnModuleInit`의 `setInterval` 폴링(기본 1500ms, `unref()`)이 디스패처를 돌린다. `NODE_ENV=test`/`OUTBOX_DISABLE_POLLING=1`에서는 폴링 미기동(누수 방지) — 테스트는 `dispatchPending`을 수동 호출. `OnModuleDestroy`에서 `clearInterval`.
- 디스패처는 **절대 reject되지 않는다**(개별 이벤트 실패를 흡수+로깅) — 앱을 죽이지 않는다.

### at-least-once + 수신측 멱등
- 폴링 디스패처는 중복 발송할 수 있다(at-least-once). ai-platform 수신측 `insert_document`가 `(file_hash, domain_code)` / `(external_id, domain_code)` `ON CONFLICT DO UPDATE`로 멱등 흡수하므로 정합하다.
- **이번에 발견·봉합한 갭**: `external_id` 경로의 conflict 타깃은 **부분 유니크 인덱스**(`uq_documents_external_id_domain WHERE external_id IS NOT NULL`, 마이그레이션 022)다. PostgreSQL은 부분 인덱스를 `ON CONFLICT` arbiter로 추론하려면 동일 술어를 명시해야 한다. 기존 SQL은 `ON CONFLICT (external_id, domain_code)`만 있어 실 DB에서 "no unique or exclusion constraint matching the ON CONFLICT specification"로 **실패**했다 → 멱등이 깨진 상태. `WHERE external_id IS NOT NULL`을 추가해 봉합했다(실 DB 재현·회귀로 고정).

## Consequences

### Positive
- 커밋成·발행失의 조용한 어긋남 제거: 이벤트가 비즈니스 커밋과 원자적으로 DB에 남으므로, 발송 실패/네트워크 단절/**프로세스 강제종료** 후에도 재기동 디스패처가 이어서 발송한다.
- 별도 인프라 0: PostgreSQL 테이블 + `FOR UPDATE SKIP LOCKED` 폴링만으로 큐를 구현 → 단일 스택 원칙 준수.
- 외부 페이로드 무변: 발송 로직 재사용으로 수신측 계약 불변.
- 멀티 인스턴스 안전: SKIP LOCKED로 중복 클레임 방지.

### Negative / Trade-off
- **발송 지연**: 폴링 주기(기본 1500ms)만큼 발행이 지연될 수 있다(이벤트 기반 LISTEN/NOTIFY로 단축 가능하나 현 단계에선 폴링으로 충분).
- **디스패처가 발송 트랜잭션 내부에서 webhook을 호출**: 발송이 느리거나 `sendWebhook`의 백오프(2/4/8s)가 길면 그 동안 클레임한 outbox 행의 잠금을 유지한다. 배치 크기를 작게(기본 20) 유지해 영향을 제한했다. 후속으로 "클레임/발송/상태전이"를 분리(클레임만 짧은 tx, 발송은 잠금 밖)하는 개선 여지가 있다.
- **FAILED 이벤트 운영 처리 필요**: MAX 도달 이벤트는 FAILED로 남는다 → 알림/재처리 운영 도구는 후속 과제.

### G20 라이브 green 전환 (후속)
- `test_g20_webhook_fire_and_forget`는 `@pytest.mark.live + xfail(strict)`다. Outbox 머지 후 **라이브 환경(KMS 컨테이너를 머지된 main으로 재빌드 + 3-서비스 + webhook 5xx 주입)**에서 본문 단언(5xx 후에도 동기화 성공)이 PASS가 되면 xfail이 XPASS(strict 실패)가 된다. 그때 xfail 마커를 제거해 green으로 고정한다. 라이브 환경 확보 전까지는 마커를 유지한다(조용한 통과 금지).

## 관련
- KMS: `feature/step18-kms-outbox` (merge `5684dd7a`, feat `d4d76897`)
- ai-platform: `feature/step18-recv-idempotency` (merge `bae3853`, fix `4c660ae`)
- Step 17b 하니스: `apps/api/tests/e2e/test_seam_failures.py::test_g20_webhook_fire_and_forget`
- 마이그레이션: KMS `20260605140000_add_outbox_events`, ai-platform `022_external_id_idempotent`
