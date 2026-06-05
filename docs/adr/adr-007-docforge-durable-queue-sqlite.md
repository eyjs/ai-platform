# ADR-007: docforge 비동기 파싱 큐 내구화 — SQLite 채택 (PostgreSQL 단일스택 예외, G21)

## Status
Accepted (2026-06-05) — 단일스택 원칙에 대한 **명시적 예외**. 사용자 검토 후 승인.

## Context

docforge(문서 파싱/OCR 서비스, 별도 `parser` 레포)의 비동기 파싱 경로는 인메모리 큐였다:

- `v1_routes.py`의 `_async_jobs: dict` + `_async_queue: queue.Queue` + 단일 daemon 워커
- 주석에 *"job store is in-process... assumes a single Gunicorn worker (DOCFORGE_WORKERS=1)"* — 영속성 0

결함(P3 Step 17 실패주입②, G21로 가시화): **워커/프로세스 재시작 시 큐의 모든 잡이 증발**한다. ai-platform `docforge_client`가 폴링하면 404(잡 소실) → `ParseError` → 재큐 없이 영구 실패. 대형 PDF(보험약관 1500p) 처리 중 재시작이면 그대로 유실된다. Step 17b가 `test_g21_docforge_job_evaporation`(`@pytest.mark.xfail(strict=True)`)으로 못 박았다.

P3 로드맵의 당초 계획(P3.3)은 **PostgreSQL `parse_jobs` 테이블 + `FOR UPDATE SKIP LOCKED`** 로, ai-platform Step 18 Outbox와 동일 기조의 단일스택 구현이었다.

### 그러나 — docforge는 어떤 PostgreSQL에도 도달할 수 없다

구현 착수 시 검증한 네트워크 현실:

| 컨테이너 | Docker 네트워크 |
|---|---|
| `docforge` | `kms-aip-shared` **만** |
| `ai-platform-postgres-1` | `ai-platform_default` 만 |
| `kms-postgres` | `kms_default` 만 |

`kms-aip-shared`는 kms-api ↔ aip-api(webhook)만 연결하고 **PG 인스턴스는 어디에도 노출돼 있지 않다.** docforge를 PG화하려면 다음을 **새로 도입**해야 한다:

1. PG 드라이버 의존성(asyncpg/psycopg) — 현재 docforge는 stdlib `sqlite3`만 사용(추가 의존성 0)
2. 네트워크 브리지 — docforge를 PG 네트워크에 연결하거나 PG를 `kms-aip-shared`에 노출(서비스간 결합 증가)
3. 스키마/마이그레이션 도구 — docforge엔 alembic/prisma가 없음. parse_jobs DB/스키마 + 마이그레이션 신설
4. (전용 PG안 선택 시) 새 PG 인스턴스 = 운영 인프라 하나 추가

## Decision

**docforge 내구 큐를 SQLite로 구현한다.** PostgreSQL 단일스택 원칙의 명시적 예외로 둔다.

- `job_store.py`: `parse_jobs` SQLite 테이블(WAL 모드), `BEGIN IMMEDIATE`로 원자적 잡 클레임(멀티워커 안전), `recover_orphans`(부팅 시 `processing` 고아 잡 회수), `cleanup_expired`(TTL 정리). docforge 기존 `storage.py`의 SQLite 영속 패턴과 동형.
- payload(원본파일)를 영속 디렉토리에 보존(기존 `shutil.rmtree` 즉시삭제 제거), 잡 완료/TTL까지 유지.
- 잡 스토어 경로를 `DOCFORGE_ASYNC_STORE_DIR`로 영속 볼륨(`/app/uploads/async_jobs`)에 고정 → **컨테이너 재생성**에도 잔존.

### 근거

- docforge는 **PG 미접속 standalone 도구**다. PG화는 결합·드라이버·마이그레이션 인프라를 *늘려* 단일스택의 *정신(운영 인프라 최소화)*에 역행한다.
- SQLite는 **임베디드**(실행 서버 0, 의존성 0)다. "Redis/Elasticsearch/MongoDB 등 추가 인프라 **서버** 도입 금지"라는 규칙의 글자를 비켜가며, 그 의도(운영 단순성)엔 부합한다. docforge는 이미 SQLite를 쓴다.
- 내구성 **목표(G21: 워커 재시작 생존 + 고아 회수)** 는 SQLite WAL + `BEGIN IMMEDIATE`로 PG SKIP LOCKED와 동등하게 달성된다.

## Consequences

**긍정**
- G21 봉합: `test_g21_docforge_job_evaporation` xfail→green(직접 실행 검증, 1 passed). 워커 재시작에도 잡 잔존·이어서 처리, 폴링 200 유지. `DOCFORGE_WORKERS=1` 제약 해제.
- 의존성/인프라 추가 0. 네트워크 결합 불변. 변경이 `parser` 레포에 국소화.

**부정 / 한계 (의식적 수용)**
- **수평 확장 한계**: SQLite는 로컬 파일 큐라 여러 docforge *컨테이너* 간 큐 공유 불가. 단일 컨테이너 내 멀티워커는 안전. docforge가 다중 인스턴스로 scale-out 되면 **그때 PG로 재검토**(이 ADR 재방문).
- **중앙 관측성 약화**: 큐를 중앙 PG/pgadmin에서 조회 불가(컨테이너 내 파일).
- **두 번째 저장기술**: PG 외 datastore가 하나 존재(임베디드라 운영 부담은 무시 가능).

## Alternatives Considered

- **(A) ai-platform/kms postgres 재사용** — docforge↔PG 네트워크 브리지 + asyncpg + 마이그레이션 신설 필요. 서비스간 DB 결합 증가.
- **(B) docforge 전용 PG 신규** — 완전 격리되나 PG 인스턴스(운영 인프라) 하나 추가 — 단일스택 정신과 상충.
- **(C) SQLite (채택)** — 결합·인프라·의존성 증가 0, 내구성 목표 달성. scale-out 시점까지 가장 단순.

## Related
- [ADR-006](adr-006-transactional-outbox-webhook-durability.md) — Seam① Outbox(G20, PG)
- P3 로드맵 Step 19 / P3.3 (단순성 트레이드오프)
