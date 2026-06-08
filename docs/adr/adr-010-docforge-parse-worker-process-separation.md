# ADR-010: docforge 파싱 워커 프로세스 분리 + 배압 (실행모델 교정)

## Status
Accepted (2026-06-08)

## Context

DB손해보험 상품 약관 PDF(69~119p) 실파싱 검증 중 docforge에서 연쇄 장애가 발생했다(상세 분석: `.pipeline/requirement.md`):

- **"Server disconnected without sending a response"**: 대형 PDF 파싱 중 CPU 100%일 때 `/v1/parse/async` submit/poll이 응답 없이 끊김 → 상류(ai-platform) 재시도 3회 소진 → 잡 영구 실패. 6개 중 1개만 적재.
- 재시작 시 내구 큐 orphan 복구 + 상류 재시도가 겹쳐 동일 문서가 큐에 중복 적체(thundering herd).
- `Semaphore(0)` OCR 데드락(별도 수정, parser `c0c49d2`).

### 근본 원인
docforge는 `gunicorn --workers 1 --threads 16`(단일 프로세스)이고, **async 파싱 워커가 그 gunicorn 프로세스 내부의 데몬 스레드 1개**(`v1_routes._async_worker_loop`, `_ensure_async_worker` lazy spawn)였다. 파싱은 CPU 바운드(페이지 루프·OCR 조율·Surya)라, **GIL 때문에 파싱이 CPU를 잡으면 gunicorn HTTP 핸들러 스레드가 GIL을 못 얻어 submit/poll에 응답 못 함**. async 큐로 디커플링한 의도가 워커가 같은 프로세스 안이라 무효였다. 또한 배압(허용제어)이 없어 큐가 무한 증가했다.

SQLite 내구 큐(`job_store.py`, ADR-007/G21)는 이미 멀티프로세스 안전했다 — WAL + `busy_timeout` + `BEGIN IMMEDIATE` 원자 `claim()`(PG `FOR UPDATE SKIP LOCKED` 등가) + `recover_orphans()`. **큐는 멀쩡했고, 빠진 것은 실행모델(소비자를 프로세스로 돌리는 것)이었다.**

## Decision

**파싱 소비자를 gunicorn 웹 프로세스 밖 별도 프로세스 풀로 분리하고, 배압을 추가한다.** (P0 = 결함 A·B. P1/P2는 후속.)

1. **별도 워커 프로세스** (`docforge-worker` 콘솔 스크립트 / `docforge/web/worker_main.py`): `DOCFORGE_PARSE_WORKERS`(기본 `min(cpu,4)`)개 프로세스가 SQLite 큐를 claim→parse→mark. 멱등 orphan 복구, SIGTERM graceful. 소비자 루프·설정은 `docforge/web/async_worker.py`로 추출(순수 이동, 파싱 알고리즘 불변).
2. **웹 무스폰**: `DOCFORGE_INPROC_WORKER=0`(기본)이면 웹은 in-proc 워커를 띄우지 않고 enqueue/poll만. `=1`은 개발용 폴백.
3. **배압**: `queued_count() >= DOCFORGE_QUEUE_MAX`(기본 16)면 `/v1/parse/async`가 **503 + `Retry-After` + `{error:{code:"QUEUE_FULL"}}`**. 무한 큐·thundering herd 차단.
4. **상류 정합**(ai-platform `docforge_client`): 503/QUEUE_FULL과 일시 연결 끊김을 진짜 실패와 구분 — `Retry-After` 백오프로 제한 횟수(기본 5) 재submit. 비-503 4xx/5xx는 `ParseError`, 폴링 타임아웃은 `ParseTimeoutError` 유지.
5. **토폴로지**: compose에 `docforge-worker` 서비스 추가(같은 이미지, `command: docforge-worker`, 같은 `uploads` 볼륨·`DOCFORGE_ASYNC_STORE_DIR` 공유, 호스트 OCR 접근 미러). `docker compose up -d`면 web+worker 동시 기동.
6. **설정 가드 일원화**: `resolve_positive_int`로 0/음수→양수 강제(`Semaphore(0)` 류 재발 차단). 시작 시 검증.

## Consequences

### 검증 (2026-06-08)
- parser: 신규 `test_worker_split`(설정 resolve/검증, 배압 503 경계, web 무스폰, inproc 폴백) + 기존 `test_job_store`·`test_v1_api`·`test_host_health` green. **회귀 0**(merged·base `c0c49d2` 동일 사전존재 실패만 — `test_v1_live` 8건은 실서버 서브프로세스 환경의존, `test_web`·`test_worker_queue` 2건). 큐 스키마·claim·파싱 알고리즘 불변.
- ai-platform: `test_docforge_client` 19 passed(503 백오프·연결끊김 제한재시도·비503 ParseError·폴링 타임아웃). 전체 단위 1025 passed/0 failed.

### 트레이드오프 / 제약
- **단일 노드 다중 프로세스** 전제(SQLite WAL은 같은 호스트 멀티프로세스에 안전). 컨테이너 *수평 다중화*(여러 호스트)는 비범위 — 필요 시 큐 백엔드 재검토(별도 ADR). ADR-007의 SQLite 선택은 유지.
- **배포 결합(중요)**: `DOCFORGE_INPROC_WORKER=0`이 기본이라, docforge 재배포 시 **반드시 `docforge-worker` 프로세스도 함께 기동**해야 파싱이 동작한다. compose에 서비스를 추가해 `docker compose up -d`로 자동화했으나, `docforge`만 단독 재기동하면 async 파싱이 멈춘다. (개발 단독 실행은 `DOCFORGE_INPROC_WORKER=1`.)
- 상류 계약 변경(503)이라 ai-platform 동반 수정으로 정합.

### 후속 (P1/P2, requirement.md)
- P1: 콘텐츠 해시 멱등(중복 submit→동일 job_id), OCR/Surya 과트리거 게이트 강화.
- P2: 호스트 OCR/VLM 짧은 타임아웃+서킷브레이커, 큐 깊이/포화 메트릭, 로깅 계약.

> **갱신 (2026-06-08, 후속 라운드 완료)**: 위 후속 중 **P1-1(콘텐츠 sha256 멱등)·P2-1(호스트 서킷브레이커+호출 타임아웃 단축)·P2-2(큐 메트릭)** 구현·검증·로컬 main 머지 완료(parser merge `c15c591`·`002c9e3`). 전부 이 ADR이 세운 실행모델 위의 robustness/관측성 보강이라 **신규 ADR 불요**(파싱 출력 불변, claim 원자성·배압·G23 자가회복 계약 보존). **P1-3(OCR/Surya 게이트)는 보류** — 파싱 *출력*을 바꿔 별도 품질검증(레퍼런스 대조) 필요. 상세는 changelog `[Unreleased]` 참조. 배포는 P0와 동일 수동 게이트(docforge+docforge-worker 함께 재빌드).

### 관련
- ADR-007(docforge SQLite 내구 큐), G21(잡 유실 봉합), G23(호스트 가용성 자가회복). parser `feature/docforge-worker-split`.
