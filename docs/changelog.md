# Changelog

모든 주요 변경 사항을 이 파일에 기록한다.
형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/)를 따른다.

---

## [Unreleased]

### Security — P0 보안 활성화 + 실행단 인가 (2026-06-10)

로드맵 P0-보안(Step 1~4)은 코드로는 기구현이었으나 플래그 기본 OFF 상태였다. 운영 전환을 실행하고, 그 과정에서 발견된 결함 2건을 봉합했으며, P1/P2의 보안 직결 항목(F19·D14 일부)을 앞당겨 적용했다.

- **strict 프로필 인가 + RLS 활성화 (A1·A2)**: `backfill_profile_authz --apply`(빈 allowed_profiles 3건 → `['*']`, default 테넌트에 12개 프로필 구체화) 선행 후 `AIP_PROFILE_AUTH_STRICT=true` + `AIP_RLS_ENABLED=true` 전환(docker-compose 공유 env). 네거티브 검증: 제한키→타프로필 403 / 빈목록키 전체거부 403 / RLS 타테넌트 raw 쿼리 0건. (merge `204885a`)
- **chat 라우트 403→500 결함 수정**: Step 22 god-file 분할 시 `routes/chat.py`의 `HTTPException` import 누락 — except 절은 예외 발생 시에야 평가되어 기존 테스트 미검출, 인가 거부가 NameError→500으로 둔갑. import 복구 + 동류 회귀 차단 테스트(`test_routes_imports.py`, AST 기반 except/raise 이름 바인딩 검증). (fix `e6a12dd`)
- **tenant_id 명시적 NULL 바인딩 방어 (4d 하드닝)**: 마이그레이션 021의 DEFAULT는 컬럼 누락에만 적용되고 명시적 NULL 바인딩에는 미적용 → vector_store/fact_store/memory.session에 `tenant_id or current_tenant.get() or 'default'` 코일레싱(session_store 기존 패턴 통일). (merge `86bc4fc`)
- **도구 실행단 하드 인가 (F19, Step 15)**: 프롬프트 레벨 제한(Profile.tools)과 별개로 실행 직전 역할 기반 차단. `tools/authz.py` + `ROLE_HIERARCHY`. registry(결정론)·tool_adapter(에이전틱) **양 경로** 체크 — 에이전틱 경로는 레지스트리를 우회하므로 둘 다 필요. `flowsns_task_actions`(전 액션 변이)에 `required_role=EDITOR` 선언. 알 수 없는 역할은 fail-closed. (merge `6a635d8`)
- **프로필 변경 시 그래프 캐시 무효화 (D14 부분)**: admin update/delete·/cache/invalidate가 GraphCache를 안 건드려 제거된 도구가 컴파일된 그래프에서 TTL(2h)까지 생존하던 구멍 봉합. 멀티 인스턴스 LISTEN/NOTIFY는 스케일아웃 시점에. (merge `de2e074`, fix `b083a0b`)
- **JWT 비대칭 RS256 전환 (D17, Step 13)**: HS256 대칭(api·bff 시크릿 공유) → RS256. bff 개인키 서명, api 공개키 검증만(api에 서명능력 제거). 토큰 헤더 alg로 검증 경로 고정 → 알고리즘 혼동 공격 차단. `kid`(공개키 지문)로 회전 대비. 과도기 HS256 폴백 유지(전환 후 `AIP_JWT_HS256_FALLBACK=false`로 퇴역). strict 활성화로 노출된 web ADMIN 락아웃은 JWT allowed_profiles 클레임 존중 + ADMIN 와일드카드로 수정. 라이브 왕복 검증 완료. (merge `0dedf90`)
- **테스트 결정성**: conftest에서 보안 플래그를 레거시 기본값으로 고정 — 개발자 `.env`가 테스트 가정을 오염시키지 않도록. 전체 **1072 passed/13 skipped/0 fail**.

### Changed — KMS 회사도메인 → 상품도메인 매핑 (분류 불일치 근본해결)

KMS는 회사중심 도메인(`DB-DAMAGE`)으로 문서를 배치하지만 ai-platform RAG·챗봇은 상품중심 도메인(`자동차보험`/`건강보험`/…)으로 검색 스코프를 건다. 동기화 시 aip-pg가 KMS 회사도메인을 그대로 `domain_code`에 적재해, 상품도메인 스코프 프로필이 DB손해보험 문서를 검색에서 배제했다(크롤한 자동차보험 6건이 챗봇에 안 떠 수동 `domain_code` 패치 → 재동기화하면 되돌아가는 데이터 패치라 근본해결). 두 분류가 이질적이고(1:1 규칙 없음) 카테고리가 웹훅에 안 실려, **KMS 웹훅 보강 + ai-platform 설정형 매핑**이 둘 다 필요한 cross-repo 변경이다. 분석·설계: [ADR-011](adr/adr-011-kms-domain-mapping.md).

- **웹훅 categoryPath 계약 (KMS)**: `document.updated` payload에 `categoryPath: string[]`(예 `["DB-DAMAGE","자동차보험","개인용"]`) 추가. `placements.service`가 docCode용 `calculateCategoryPath`를 재사용해 계산하되, **트랜잭션 밖**에서 미리 구해 넘긴다(`resolveCategoryPathForSync`) — 카테고리 조회 I/O로 인터랙티브 트랜잭션 창을 넓히지 않는다(G20 정렬). `enqueue(tx, …)`는 기존 `$transaction` 안에 그대로 유지(원자성 보존), 디스패처 불변. 계산 실패 시 `[domainCode]` 폴백 — enqueue를 막지 않는다(하위호환). — KMS (merge `5c741270`, feat `cab9a992`)
- **설정형 도메인 매핑 (ai-platform)**: `seeds/domain_mapping.yaml`로 `(회사도메인, categoryPath) → 상품도메인` 매핑을 외부화(`DB-DAMAGE`: 자동차보험→자동차보험, 장기보험/건강→건강보험, 장기보험/간병→간병보험, 일반보험/화재→화재보험). 새 상품/카테고리는 코드 변경 없이 이 파일만 갱신(운영성). 로더 `domain_mapping.resolve_product_domain(domain, category_path)`가 `categoryPath[1:]`를 가장 구체적(긴 경로)부터 매칭하고 미매핑/부재 시 `None`. 프로세스 1회 로드+캐시, 파일 부재/파싱오류/스키마 위반은 방어적 흡수(WARN). — ai-platform
- **동기화 적용 + 조용한 누락 0 (ai-platform)**: `kms_sync.sync_document`가 `domain_code` 확정 직후 매핑을 적용 — 매핑되면 상품도메인으로 치환(`documents`·`document_chunks` 동일), 미매핑/부재(구 KMS·타 consumer·매핑 미정의 도메인 예 HANHWA)면 **회사도메인 fallback + `kms_sync_domain_unmapped` WARN**으로 가시화(조용한 누락 0). KMS=분류 SoT(ADR-009), ai-platform은 해석만. — ai-platform (merge `c3da04e`, feat `2742cab`)

### Verification (도메인 매핑 근본해결)

- ai-platform: 신규 매핑 단위테스트 16건 — 6경로 매핑·구체성 우선(`장기보험/건강` > `장기보험`)·미매핑(`장기보험/종합`)·빈배열·도메인코드만·미정의 도메인(HANHWA)·`kms_sync` 적용(자동차보험/건강 치환)·미매핑·categoryPath 부재 fallback+WARN. 전체 단위 **1041 passed/9 skipped/0 fail**(`--ignore=tests/e2e`). Step17 E2E 비라이브 게이트 불변.
- KMS: `npx tsc --noEmit` green. 신규 `placements.service.spec` 5건(트랜잭션 밖 경로 계산·폴백, 트랜잭션 안 payload categoryPath 적재·동일 tx) + outbox(G20) 10건 포함 api 스위트 **42 passed**(회귀 0).
- 계약 정합: KMS payload `categoryPath` ↔ ai-platform `data.get("categoryPath")` 소비 일치 확인.

> **머지 상태**: KMS·ai-platform 각각 로컬 main에 feature 브랜치를 no-ff 머지(파일 디스조인트·다른 repo라 충돌 0). **origin push 안 함**(최종 결정은 사용자, origin/main 불변: KMS `edf2ce21`, ai-platform `b736b6b`). 워크트리 정리 완료, feature 브랜치 보존.
> **배포·backfill 수동 게이트**: 계약 성립에 KMS(kms-api)+ai-platform(worker) **동반 재배포** 필수(KMS만 재배포 시 ai-platform이 옛 코드로 categoryPath 무시 → 여전히 fallback). 기존 DB손해보험 문서(수동 패치 6건 포함)는 재동기화(placement touch/재sync 트리거)로 매핑 경로 재적재 — 멱등(external_id UPSERT). **현재 가동 컨테이너 미반영.**

### Changed — docforge 파싱 워커 프로세스 분리 + 배압 (아키텍처 P0, 결함 A·B)

DB손해보험 약관 PDF(69~119p) 실파싱 검증 중 드러난 docforge 실행모델 결함을 교정했다. 근본 원인: 파싱(CPU 바운드)을 `gunicorn --workers 1` **웹 프로세스 내부 데몬 스레드 1개**로 돌려, 대형 문서 파싱이 GIL을 잡으면 HTTP 핸들러가 굶어 submit/poll이 "Server disconnected"로 끊김 → 상류 재시도 소진 → 잡 영구 실패. 배압도 없어 큐 무한증가·thundering herd. SQLite 큐(`job_store`)는 이미 멀티프로세스 안전이라 **소비자만 프로세스화**. 분석·설계: [ADR-010](adr/adr-010-docforge-parse-worker-process-separation.md).

- **파싱 워커 프로세스 분리 (docforge)**: `docforge-worker` 콘솔 스크립트(`worker_main.py`) — `DOCFORGE_PARSE_WORKERS`(기본 `min(cpu,4)`)개 프로세스가 SQLite 큐를 claim→parse→mark. 소비자 루프·설정을 `async_worker.py`로 추출(파싱 알고리즘 불변). 멱등 orphan 복구 + SIGTERM graceful. 웹은 `DOCFORGE_INPROC_WORKER=0`(기본)으로 enqueue/poll만 → GIL 격리. compose에 `docforge-worker` 서비스(같은 이미지·`uploads` 볼륨 공유). — parser (merge cd8ec02, feat ad6f928)
- **배압 (docforge)**: `queued_count >= DOCFORGE_QUEUE_MAX`(기본 16) → `/v1/parse/async`가 **503 + Retry-After + `{error:{code:"QUEUE_FULL"}}`**. 무한 큐·thundering herd 차단. 설정 가드 일원화(`resolve_positive_int`, 0/음수→양수)로 `Semaphore(0)` 류 재발 방지. — parser
- **상류 503 배압 처리 (ai-platform)**: `docforge_client.parse()`가 503/QUEUE_FULL·일시 연결끊김을 진짜 실패와 구분 — `Retry-After` 백오프로 제한 횟수(기본 5) 재submit. 비-503 4xx/5xx는 `ParseError`, 폴링 타임아웃은 `ParseTimeoutError` 유지. — ai-platform (merge db08b59, feat e25499e)

### Verification (docforge P0)

- parser: 신규 `test_worker_split`(설정 검증·배압 503 경계·web 무스폰·inproc 폴백) + 기존 `test_job_store`·`test_v1_api`·`test_host_health` green. **회귀 0** — merged·base `c0c49d2` 동일 사전존재 실패만(`test_v1_live` 8건=실서버 환경의존, `test_web`·`test_worker_queue` 2건). 큐 스키마·claim·파싱 알고리즘 불변.
- ai-platform: `test_docforge_client` 19 passed, 전체 단위 1025 passed/0 failed.

> **배포 결합 주의**: `DOCFORGE_INPROC_WORKER=0` 기본이라 docforge 재배포 시 **`docforge-worker` 프로세스도 함께 기동 필수**(`docker compose up -d`가 둘 다 띄움). `docforge`만 단독 재기동하면 async 파싱이 멈춘다. **현재 가동 컨테이너는 미반영 — 재빌드는 수동 게이트.** P1(콘텐츠 멱등·OCR 게이트)·P2(서킷브레이커·메트릭)는 후속.

### Changed — docforge 콘텐츠 멱등성 + 호스트 서킷브레이커 + 큐 메트릭 (아키텍처 P1·P2, 결함 C·F·G)

P0(워커 프로세스 분리+배압)의 후속. 같은 실행모델 위에서 **신뢰성(멱등)·견고성(서킷브레이커)·관측성(메트릭)**을 보강했다. 전부 parser(docforge)이고 **파싱 *출력* 불변** — claim 원자성·배압(P0)·G23 자가회복(TTL 재프로브) 계약을 보존한다. 설계 맥락: [ADR-010](adr/adr-010-docforge-parse-worker-process-separation.md) 후속 절. **P1-3(OCR/Surya 게이트)는 보류** — 파싱 출력을 바꿔 별도 품질검증이 필요(이번 스코프 밖).

- **콘텐츠 멱등성 (P1-1, docforge)**: `/v1/parse/async`가 업로드 바이트 `sha256`을 키로, 동일 해시+mime가 **in-flight(queued/processing)**면 새 잡을 만들지 않고 **기존 `job_id`를 202로 반환**(`deduplicated: true`). 상류 재시도·orphan 복구·thundering herd로 인한 중복 파싱과 "빈 적재가 좋은 적재를 덮는 레이스"를 제거. `job_store.enqueue_idempotent()`가 조회+삽입을 단일 `BEGIN IMMEDIATE`로 묶어 동시 동일-바이트 submit도 단일 승자(claim 계약과 동형, 미변경). `parse_jobs.content_hash` 컬럼은 **하위호환·멱등 마이그레이션**(`PRAGMA table_info` 점검 후 `ALTER ADD COLUMN`) — 기존 store 부팅·2회 부팅·레거시 NULL 행 전부 안전, 부분 인덱스(queued/processing)로 조회 경량. done 잡은 dedup 비대상(재처리 허용). — parser (merge `c15c591`, feat `e572475`)
- **호스트 서킷브레이커 (P2-1, docforge)**: `host_health.TTLAvailability`에 연속 실패 카운터 결합 — `DOCFORGE_HOST_CB_THRESHOLD`(기본 3) 연속 프로브 실패 시 **open**: `DOCFORGE_HOST_CB_COOLDOWN_SEC`(기본 30s) 동안 프로브 없이 즉시 `False`(네트워크 블록 0). cooldown 후 **half-open** 1회 프로브 → 성공 시 closed 회복·카운터 리셋, 실패 시 재 open. 성공 프로브는 항상 카운터를 리셋해 **정상 호스트는 단발 블립으로 트립되지 않음**(보수적). 죽은 OCR/VLM 호스트가 페이지마다 재프로브·호출되며 파싱 스레드를 잡는 문제 차단. **G23 TTL 재프로브와 대체가 아닌 결합**(open 체크가 TTL/invalidate보다 우선 → known-down 호스트 stampede 방지). — parser (merge `002c9e3`, feat `e8c1ef3`)
- **호스트 호출 타임아웃 단축 (P2-1, docforge)**: 원격 OCR `urlopen` 60→**30s**(`DOCFORGE_OCR_CALL_TIMEOUT_SEC`), VLM 120→**45s**(`DOCFORGE_VLM_CALL_TIMEOUT_SEC`), env override. 한 페이지가 죽은/느린 호스트에 최대 1~2분 블록되던 worst-case를 단축. `probe_health`(3/5s)·graceful degrade(빈 결과)는 불변. — parser
- **큐 메트릭 (P2-2, docforge)**: `job_store.counts()`(단일 GROUP BY로 status별 카운트) + 신규 `GET /v1/metrics` 및 `/v1/health` 확장에 `queue_depth`(queued)·`in_flight`(processing)·`queue`(queued/processing/done/failed breakdown) 노출. `/health`는 메트릭 조회가 실패해도 200 유지(degrade). 큐 깊이·포화를 별도 스크레이프 없이 관측. — parser

### Verification (docforge P1·P2)

- parser: 신규 테스트 — 멱등(동일 파일 2회 submit→**동일 job_id·INSERT 1건**, 다른 파일/mime→별 잡, done은 재처리 허용)·마이그레이션(레거시 컬럼 없는 DB 부팅·2회 부팅·NULL 행 보존)·메트릭(`counts()`·`/v1/metrics`·`/v1/health`)·서킷(단발 미트립·3회 연속→open·open 중 프로브 0·half-open 회복/재open·설정 override·죽은 호스트 타임아웃 전파). 기존 `test_job_store`·`test_v1_api`·`test_worker_split`·**`test_host_health`(G23) 16건**·어댑터(cloud_vlm/vision_llm) 회귀 green. 필수 그린 스위트 **152 passed**, 전체 단위 **1322 passed/2 failed**(2건은 base `cd8ec02` 사전존재: `test_web::test_editor_redirects_to_verify`·`test_worker_queue::test_backward_compat_pending_status`; `test_v1_live` 8건은 실서버 환경의존 — **회귀 0**). ruff 신규 위반 0(잔존 6건 전부 사전존재, 어댑터 import-sort 2건은 오히려 정리). 순환 import 0. 런타임 E2E 스모크(멱등·메트릭·서킷) 검증.
- **배포는 P0와 동일 수동 게이트** — docforge+docforge-worker **함께** 재빌드(P0 토폴로지 유지). 워크트리 격리 테스트로만 검증했고 실배포·재빌드는 하지 않음.

> **머지 상태**: parser 로컬 main에 두 feature 브랜치를 순차 no-ff 머지(파일 디스조인트라 충돌 0). **origin push 안 함**(최종 결정은 사용자). 워크트리 정리 완료, feature 브랜치 보존.

### Changed — Step 22: god-file 분할 (G25, P3 마지막 — 순수 이동, 동작·회귀 0)

두 1000줄+ god-file을 **동작 무변 순수 이동(pure-movement) 리팩터**로 도메인 경계별 모듈로 분할했다. 외부 동작·응답·라우트 경로·공개 import·호출 순서 전부 불변. AST 함수 본문 대조로 순수이동 증명(본문 변경 0), 양쪽 회귀 0.

- **ai-platform `gateway/router.py`(1327줄) → `gateway/routes/*` 분할**: 도메인 경계별 모듈(`public`=/health·/profiles, `chat`=/chat·/chat/stream, `ingest`=/documents/ingest·/chat/sessions/{id}/files·/documents/ingest/{job_id}, `workflow`, `admin`=/api-keys, `feedback`, `session`)로 라우트를 이동하고, 공용 로직(`_authenticate`/`_check_rate_limit`/`_save_extracted_memories`/`_resolve_session_scope_id`/graceful-shutdown 카운터/`_prepare_chat`·`_prepare_chat_fast`/`_step_to_response`/`APP_VERSION`)을 `routes/helpers.py`에 집결. `routes/__init__.py`가 분할 전 등록 순서 그대로 `gateway_router`를 조합. `router.py`는 `gateway_router`/`APP_VERSION`/`wait_for_pending_requests`를 재수출하는 얇은 facade로 축소 → **`main.py` import 무변경**. routes/* 상호 import 0(`helpers`만 의존 → 순환 없음). 레이어 규칙(Gateway→Router→Agent→Tool, Profile 하드코딩 0) 유지. **라우트 인벤토리 불변**(15 routes 동일 경로·메서드·순서). — ai-platform (merge 0049fab, refactor b18f0cf)
- **parser `usecases/page_processor.py`(1177줄) → helpers 믹스인 분할 (Option A)**: 공개 `page_processor.py`는 `PageResult` + `PageProcessor.__init__`/`process()` 오케스트레이션만 유지(공개 인터페이스·import 경로 불변), 13개 `_*` 헬퍼 메서드를 `_page_processor_helpers.py`의 `_PageProcessorHelpers` 믹스인으로 이동(`PageProcessor` 상속 → MRO로 동일 해석). **G23 결합 보존**: `llm_engine.describe_image()`/`is_available()` 호출 경로·순서·인자 불변(host_health TTL 재프로브 회귀 방지). 역참조(`parse_pdf`/`pipeline_coordinator`/`page_reprocessor`/`_parse_pdf_helpers`)·`_PageResult` 별칭·로거 이름 불변. 순환 import 0(`TYPE_CHECKING` 보호). — parser (로컬 main 머지 93fd981, refactor 838eb97, origin push 안 함)

### Fixed — 테스트 (Step 22, 그린 전제 복원)

- **`test_docforge_client` 3건 그린 복원** (분할 전 그린 전제): `docforge_client.parse()`는 비동기 2단계(POST `/v1/parse/async` submit→`data.job_id`→GET 폴링→`status:done`)로 동작하나 테스트 mock이 구 동기 프로토콜이라 사전존재 실패(`KeyError: 'job_id'`)였다. **프로덕션 코드 무변경**, mock만 실제 클라이언트 흐름에 정렬. 408 케이스는 클라이언트 진실에 맞춰 분리: submit 408→`ParseError`(실제 동작), 폴링 `max_wait` 초과→`ParseTimeoutError`(폴링 타임아웃 전용 경로). 전체 단위 1015 passed/3 failed → **1019 passed/0 failed**. — ai-platform (merge a17c3c5, test ba9a5b9)

### Verification (Step 22)

- **ai-platform**: 전체 단위 `pytest tests/ --ignore=tests/e2e` **1019 passed / 9 skipped / 0 failed**(분할 전후 동일), Step 17 E2E 비라이브 게이트 **2 passed / 4 skipped** 불변, 라우트 인벤토리 15 routes 불변. AST 함수 본문 대조: 29개 함수 본문 100% 동일(데코레이터 모듈-로컬 router명 정규화 후 경로·메서드·kwargs 동일).
- **parser**: target(`test_page_processor`·`test_pipeline_coordinator`·`test_sprint6_p0`·`test_host_health`) **85 passed**, 어댑터(cloud_vlm/vision_llm/image_vlm/region_vlm) 회귀 green. **전체 unit 1203 passed / 4 failed = base 63dff7b 동일**(사전존재 실패 4건 `test_web`·`test_worker_queue`만 잔존 — 분할 무관, 회귀 0 증명). AST 대조: 19개 함수/메서드 본문 100% 동일, `PageResult` 11필드 동일. ruff 신규 findings 0(잔존 13건은 원본에서 그대로 이동된 사전존재 스타일 — 순수이동 가드로 미수정). parser 미커밋 5종(value_objects/document_intelligence/markdown_assembler/scripts.__init__/vlm_service.log) 머지 전후 shasum 동일(불가침 보존).

> **순수 이동 가드**: G25는 분할만 수행한다. 로직/시그니처/응답/호출순서 변경 0, 기능 추가 0, 범위 외 reformat 0. ADR는 작성하지 않는다(순수 리팩터, 결정 사항 없음). G20/G21/G23/G24 산출물 불침범.

### Changed — Step 21: RAG 진실원천 확정 + KMS 죽은 스키마 드롭 (G24, 이중스토어 해소)

- **RAG 단일 진실원천 = ai-platform pgvector(aip-pg `document_chunks`) 확정**, 레이어 책임 계약 명문화: [layer-responsibility.md](architecture/layer-responsibility.md) — KMS=원문/배치 SoT, ai-platform=RAG SoT, docforge=stateless 파싱, 동기화=outbox→webhook 단일경로.
- **KMS 죽은 RAG 스키마 드롭** (택1 (A)정리, 2026-06-08 사용자 확정): ai-worker 완전 제거(KMS `fae75cf8`) 후 reader/writer 0건이던 raw SQL 잔재 7개 테이블(`document_chunks` 17,307행 · `document_contents` 1,023행 · `processing_jobs` 2,727행 · `chat_sessions`/`chat_messages`/`chat_turns`/`intent_examples` 0행) + 미사용 pgvector extension을 `20260608000000_drop_dead_rag_schema` 마이그레이션으로 제거. 드롭 전 전체 pg_dump 백업(`~/Backups/kms-dead-rag-tables-20260608.sql.gz`). aip-pg 동명 테이블과의 이중스토어 혼선 해소. — KMS (feature/step21-rag-sot)

### Fixed — E2E 하니스 bitrot 3건 (Step 21, 라이브 골든패스 복구)

- **업로드 기본 확장자 `.txt`→`.csv`** (`_harness.py`): KMS multer 허용(`.pdf/.md/.csv`)과 ai-platform ingestion 허용(`pdf/csv/xlsx/xls`)의 교집합(pdf/csv)으로 정렬. `.txt`는 KMS가 500으로 거부해 골든패스가 라이브에서 실행 불가였다. content도 호출마다 고유화(KMS 동일내용 재업로드 409 회피).
- **KMS JWT `sub`에 실 user UUID 주입** (`conftest.py`): `AIP_E2E_KMS_USER_ID` env — 라이브 KMS는 `sub`를 `documents.created_by`(UUID)로 기록하므로 더미 문자열이면 Prisma UUID 오류.
- **동기화 대기 30s→90s** (`test_kms_to_rag.py`): docforge 일시 장애 시 job_queue 재시도 1사이클(딜레이 30s)을 흡수. 30s는 정상경로만 커버해 재시도 한 번에도 플레이키였다.

### Decision (Step 21)

- [ADR-009](adr/adr-009-rag-single-source-of-truth.md): RAG SoT = ai-platform pgvector. 죽은 동명 스키마는 잘못된 문서 역할을 하므로 (B)보류가 아닌 (A)드롭. 향후 KMS 자체 RAG는 새 마이그레이션으로 재구축(금지 대상은 *죽은 스키마 방치*).

> **Step 17 E2E 풀 라이브 green (2026-06-08).** 드롭 후 골든패스 3건이 **처음으로 전부 라이브 통과**(5 passed / 1 skipped — 유일한 skip은 G20 라이브주입 게이트, Step 18에서 기실증): KMS 업로드 → outbox(전부 SENT) → webhook → job_queue → docforge 파싱 → 임베딩 → aip-pg 단일행 + 멱등 재적재. 드롭이 살아있는 경로를 건드리지 않았음을 계약이 아닌 실동작으로 증명. (참고: `tests/test_docforge_client.py` 3건 실패는 클린 main에서도 동일한 사전존재 — Step 21 무관, 후속 수리 대상.)

### Fixed — Step 20: docforge 호스트 엔진 가용성 자가회복 (G23, Seam③)

- **가용성 영구 캐시 제거 → TTL 재프로브 (docforge)**: `apple_vision_remote.py`·`host_vlm_engine.py`의 `is_available()`가 `self._available`에 결과를 **영구 캐시**(한 번 False면 영구 → 호스트 OCR/VLM 재기동해도 docforge 죽은 채 유지, 자가회복 불가)했다. 공통 `host_health.TTLAvailability`로 교체 — `time.monotonic` 기준 TTL(기본 30s, `DOCFORGE_HOST_PROBE_TTL_SEC`) 경과 시 재프로브 + 원격 호출 실패 시 `invalidate()`로 즉시 재프로브. 재기동된 호스트 서비스를 docforge가 **스스로 다시 잡음**(다음 페이지/잡에서 자동 정상화). graceful degrade(다운 동안 빈 결과)는 보존. — parser (merge 63dff7b, feat 1c14b63)
- **경량 헬스 폴러 (docforge)**: `HostHealthPoller`(stdlib daemon thread)가 OCR:5052·VLM:5053·임베딩:8103 `/health`를 주기 핑하고 **상태 전이(up↔down)만** 로깅. 관측 전용·**자동 시작 안 함**(명시 `start()`) → 기존 동작 무변. `probe_health`가 `status:ok`(OCR/VLM)·`status:healthy`(임베딩) 스키마를 모두 흡수. 자동기동은 범위 외(문서화 대체). — parser
- **G23 green 전환 (ai-platform)**: `test_g23_ocr_availability_cache_sticky`(`xfail strict`)를 `test_g23_ocr_availability_reprobe_recovery`(contract, green)로 반전. `_injection.py`의 `_StickyAvailability`(영구 False) → `_RecoverableAvailability`(TTL 재프로브 자가회복), 회복이 재프로브에서 비롯됨을 `probe_count`로 증명(가짜 통과 방지). — ai-platform (merge, test 98041b1)

### Added — 테스트 (Step 20)

- parser: `test_host_health.py` — TTL 만료 재프로브, 캐시 유지, 실패→복구 전이, `invalidate` 즉시 재프로브, 폴러 전이 로깅, `probe_health` 스키마(ok·healthy), 어댑터 자가회복(통합) (16 passed)

### Decision (Step 20)

- [ADR-008](adr/adr-008-docforge-host-engine-availability-self-recovery.md): 호스트 엔진 가용성 **영구 캐시 → TTL 재프로브** 자가회복. 자동기동은 범위 외(재기동을 스스로 감지해 다시 잡을 뿐, 기동은 운영자/launchd 책임). 추가 인프라·의존성 0(stdlib).

> **G23 라이브 자가회복 실증 (2026-06-05).** 실 `AppleVisionRemoteEngine`·실 호스트(:5052 up / :5053 down / :8103 healthy)·실 wall-clock TTL(1s)로: 다운→False 캐시 → 복구돼도 TTL 내 False(캐시 증명) → 1.2s sleep으로 TTL 경과 → 재프로브 → True + `host engine recovered (re-probe)` 로깅. 구 영구캐시면 영원히 False였을 가용성이 장애를 넘어 자동 복구. (컨테이너 내부 다운→재기동 실증은 docforge 재빌드 후 수동 게이트.)

### Fixed — Step 19: docforge 파싱 잡 유실 봉합 (G21, Seam②)

- **내구 잡 큐 (docforge)**: 인메모리 `_async_jobs`/`_async_queue`(단일 워커, 재시작 시 전손실)를 **SQLite 내구 잡 스토어**(`job_store.py` — WAL + `BEGIN IMMEDIATE` 원자 클레임, 부팅 시 `processing` 고아 잡 회수, payload 영속 보존, TTL 정리)로 교체. 워커/프로세스 재시작에도 잡이 잔존·이어서 처리되고 폴링 200을 유지(404 증발 제거). `DOCFORGE_WORKERS=1` 제약 해제. — parser (merge 9b63d39)
- **내구성 경계 보강**: 잡 스토어 경로를 `DOCFORGE_ASYNC_STORE_DIR`로 영속 볼륨(`/app/uploads/async_jobs`)에 고정 → 컨테이너 재생성에도 큐 잔존. — parser `eb0ceef`
- **G21 green 전환 (ai-platform)**: `test_g21_docforge_job_evaporation`의 `xfail(strict)` 제거, "재시작 견딤→재처리→파싱 성공" green 단언으로 반전. `_injection.py`에 `make_docforge_durable_restart_transport` 추가(제출→processing×N→done 계약 재현). — ai-platform (merge 9949684)

### Added — 테스트 (Step 19)

- parser: `test_job_store.py` + `test_v1_api.py` — enqueue→INSERT, 동시 claim 단독성, 재시작 고아 회복→재처리, 결과 UPDATE, 라우트 폴링 200 유지, TTL 정리, 워커 e2e (42 passed)

### Decision (Step 19)

- [ADR-007](adr/adr-007-docforge-durable-queue-sqlite.md): docforge 내구 큐를 **SQLite로 구현(PostgreSQL 단일스택 예외, 사용자 승인)**. docforge가 PG에 네트워크 도달 불가한 standalone 도구라, PG화가 결합·드라이버·마이그레이션 인프라를 늘려 단일스택의 정신에 역행. 임베디드 SQLite(서버 0, 의존성 0)로 G21 봉합. scale-out 시 PG 재검토.

### Fixed — Step 18: KMS→ai-platform 동기화 발행 내구화 (G20, Seam①)

- **Transactional Outbox (KMS)**: fire-and-forget webhook 발행을 비즈니스 트랜잭션 내부 `outbox_events` 적재 + 폴링 디스패처 재시도로 봉합. 커밋成·발행失(웹훅 5xx / 네트워크 단절 / 프로세스 강제종료)로 인한 RAG 미동기를 제거. PostgreSQL 단일스택(테이블 + `FOR UPDATE SKIP LOCKED` 폴링, Redis/Kafka 0). 외부 webhook 페이로드 무변. — KMS `feature/step18-kms-outbox` (merge 5684dd7a)
- **수신측 멱등 갭 봉합 (ai-platform)**: `insert_document`의 `external_id` 경로 `ON CONFLICT (external_id, domain_code)`가 부분 유니크 인덱스(`uq_documents_external_id_domain WHERE external_id IS NOT NULL`, 마이그레이션 022)를 arbiter로 추론하려면 동일 술어 명시가 필요한데 누락되어 실 DB에서 "no unique or exclusion constraint matching..."로 실패(=at-least-once 중복 수신이 멱등 아님). `WHERE external_id IS NOT NULL` 술어 추가로 봉합 (`src/infrastructure/vector_store.py`). — ai-platform `feature/step18-recv-idempotency` (merge bae3853)

### Added — 테스트

- KMS: `outbox.service.spec.ts` — 단위 7(enqueue PENDING, SENT/재시도/FAILED 상태전이, 화이트리스트, 빈 구독자 SENT) + 실 DB 통합 3(내구성: 디스패처 미기동 enqueue → 새 인스턴스가 집어 SENT / 재시도 수렴 / 트랜잭션 원자성)
- ai-platform: `test_insert_document_idempotency_db.py` — 실 DB 회귀: `(external_id, domain)` / `(file_hash, domain)` 중복 2회 → 행 1개·동일 id·UPDATE 수렴, 식별자 없음 → 신규 2행(계약). DB 미가용 시 명시적 skip.

### Decision

- [ADR-006](adr/adr-006-transactional-outbox-webhook-durability.md): Transactional Outbox로 webhook 발행 내구화 (Kafka/Redis 대신 PostgreSQL 테이블 + 폴링)

> **G20 라이브 green 검증 완료 (2026-06-05).** KMS 컨테이너를 머지 main으로 재빌드 → 수신부(aip-api) 정지 주입 → 배치 커밋 시 outbox 이벤트가 PENDING으로 잔존(유실 0), ai-platform 미동기 → 수신부 복구 시 디스패처 재시도로 SENT 전환 + ai-platform `documents` 행 출현(RAG 동기화 회복) 확인. 구 fire-and-forget이면 영구 유실됐을 문서가 장애를 넘어 도달.

### Fixed — Step 18 후속: Outbox 디스패처 트랜잭션 결함 (G20 라이브 검증서 발견)

- **디스패처 네트워크 I/O를 트랜잭션 밖으로 (KMS)**: `dispatchPending`이 webhook 발송(내부 재시도 ~6s)을 Prisma 인터랙티브 트랜잭션(타임아웃 5s) 안에서 수행 → 발송 지연·실패 시 트랜잭션이 먼저 만료되어 상태전이 update가 무효화. attempts/last_error 미기록(0 고정)으로 백오프·dead-letter(FAILED)·관측성이 깨지고, 느린 성공(>5s)은 SENT 유실→중복 재전송. **수정**: 클레임(PENDING→SENDING 원자적, FOR UPDATE SKIP LOCKED, 짧은 tx) → 발송(tx 밖) → 종결(짧은 update) 3단계 분리 + 고아 SENDING lease 회수. 라이브 재검증: 지속 실패 시 attempts 0→1→2→3 증가, Transaction-closed 에러 0, 복구 후 SENT+RAG 동기화. — KMS `0aea523a`

---

## [0.11.0] — 2026-05-07

### Added
- **Workflow Action Step**: 새 `action` step type — YAML 설정만으로 외부 API 호출 가능 (`src/workflow/action_client.py`, `src/workflow/template.py`)
- **WorkflowSessionStore**: PostgreSQL 기반 워크플로우 세션 영속화 (`src/workflow/session_store.py`) — 서버 재시작 후에도 세션 유지
- **Agentic Graph LRU Cache**: `(profile_id, frozenset(tool_names))` 복합 키로 LangGraph 인스턴스 캐싱 — 동일 프로필 두 번째 요청부터 graph 재빌드 제거 (`src/agent/graph_executor.py`)
- **Prompt Caching**: OpenAI/Anthropic provider에 `cache_control` 헤더 적용 — system prompt 캐싱으로 LLM 비용 절감 + 응답 지연 감소 (`src/infrastructure/providers/llm/openai.py`, `anthropic.py`)
- **Plan-and-Execute 아키텍처**: Planner → Adaptive Retry → Guardrail Regen 3단계 Agent 실행 (`src/agent/planner.py`, `src/agent/nodes.py`)
- Per-workflow escape keywords (`WorkflowDefinition.escape_keywords`)
- Workflow template rendering (`src/workflow/template.py`)

### Changed — 성능 최적화 (7 tasks, 4 phases)
- **Hybrid Search 병렬화**: vector/FTS/trgm 3개 서브쿼리를 `asyncio.gather`로 동시 실행 (`src/infrastructure/vector_store.py`)
- **RAG 병렬화**: neighbor expansion N개 개별 쿼리 → `WHERE id = ANY($1)` 단일 IN 쿼리, multi-query search `asyncio.gather` 병렬 실행 (`src/tools/internal/neighbor_expander.py`, `rag_search.py`)
- **Session Memory SQL-level JSONB slice**: Python-level 전체 읽기 후 슬라이스 → DB에서 마지막 N개만 추출 (`src/infrastructure/memory/session.py`)
- **DB Pool 파라미터 명시적 설정**: SQLAlchemy AsyncEngine에 `pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle` 추가 (`src/bootstrap.py`)
- `pg_pool_min` 5→2, `pg_pool_max` 50→20 (overprovisioning 제거)
- Workflow Engine 전체 async 전환, `resume()` 경로 이중 `_save_session` 제거

### Changed — 파싱
- `ParsingEngine` DocForge 완전 위임 — PyMuPDF/로컬 PDF 분석기 제거
- `pdf_analyzer.py` 삭제, `pdf_parser.py` 삭제 (DocForge 대체)

### Removed
- `csv_parser.py`, `excel_parser.py` (DocForge 위임 완료)
- `pdf_analyzer.py`, `pdf_parser.py` (DocForge 위임 완료)
- PyMuPDF 의존성

### Fixed
- 리뷰 지적사항: 깨진 유니코드 복원, 에러 아이콘 배경색 구분
- DocForge 인증 헤더 누락 수정

---

## [0.10.0] — 2026-05-04

### Added
- `DocForgeClient` for async HTTP communication with DocForge parsing service (`src/pipeline/parsing/docforge_client.py`)
- 3 new config settings: `docforge_url`, `docforge_timeout_sec`, `docforge_fallback_enabled`

### Removed
- Local CSV parser (`csv_parser.py`) -- delegated to DocForge
- Local Excel parser (`excel_parser.py`) -- delegated to DocForge
- Docling integration and VLM OCR references
- `openpyxl` from ai-platform dependencies (now DocForge-only)
- 5 deprecated config settings: `parser_enable_docling`, `parser_enable_vlm`, `vlm_ocr_endpoint`, `parser_csv_max_rows`, `parser_excel_max_rows`

### Changed
- `ParsingEngine` constructor: `docforge_url`/`docforge_timeout_sec`/`docforge_fallback_enabled` replace `enable_docling`/`enable_vlm`
- `PdfParser`: DocForge replaces Docling/VLM for non-TEXT_ONLY PDFs
- `pdf_analyzer`: recommended_parser values unified to "docforge" (was "docling"/"vlm")
- `test_parsing_engine.py` fully rewritten for DocForge-based architecture

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
