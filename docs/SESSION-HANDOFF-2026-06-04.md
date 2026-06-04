# 세션 핸드오프 — 인제스천 모듈화 + docforge 안정화 (2026-06-04)

> 목적: 다음 컨텍스트에서 **시스템 안정성·완성도를 평가**하기 위한 자급자족 핸드오프.
> 이 세션에서 실측·수정한 내용과, 남아 있는 구조적 약점을 정직하게 기록한다.

## 0. TL;DR

- **원 과제**: DB손해보험 상품공시(idbins.com) 문서를 KMS DB손해보험 섹션에 적재하고
  챗봇 검색(문서식별·메타데이터·관계·비교)을 검증 → **미완**(인프라/파싱 안정화에 세션 소진).
- **실제로 한 것**: ① 깨진 파싱·이중트리거·죽은경로 등 **구조 결함을 다수 수정**,
  ② 인제스천 진입점을 **모듈화**(새 업로더가 코어 수정 0으로 붙음), ③ docforge **비동기 큐화**,
  ④ 보안 결함(IDOR) 수정. 모두 **3개 repo main 에 커밋·머지 완료**.
- **시스템 총평**: 핵심 경로는 동작하나 **외부 호스트 서비스 의존·통합테스트 부재·이중 스토어**로
  여전히 깨지기 쉽다. 아래 §5 참조.

## 1. 시스템 구성 (업로드 → 검색가능 지식)

3개 서비스 + PostgreSQL.

```
업로더 ─▶ KMS(NestJS, :3001)  ─webhook─▶ ai-platform(FastAPI, :8020) ─HTTP─▶ docforge(Flask, :5051)
          packages/api        /api/webhooks/kms   apps/api               /v1/parse/async
          문서저장+배치        → kms_sync 큐        IngestPipeline          파싱(OCR/VLM 호스트브리지)
                                                   파싱→청크→임베딩→pgvector
```

- **docforge 호스트 의존 서비스** (모두 macOS 호스트 프로세스, `host.docker.internal`):
  - OCR `:5052` (`parser/ocr_service.py`, Apple Vision) — **이 세션 중 다운→행 유발**. `ocr_service.py` 로 기동.
  - VLM `:5053` (`parser/vlm_service.py`, Qwen2-VL MLX) — **다운 + 모델 미가용**. graceful degrade.
  - 임베딩 `:8103` (MLX, launchd `com.kms.mlx-embedding`) — **부하로 교착됨, 재기동함**.
- **포트/컨테이너**: aip-api `8020`, kms-api `3001`, docforge `5051`, aip-postgres `5434`(db `ai_platform`/user `aip`), kms-postgres `5432`(db/user `kms`).
- **인증**: ai-platform API키 `aip_dev_admin`(=dev-admin-key, ADMIN). KMS JWT는 `dev_jwt_secret_key_32_chars_min` 로 발급(admin user `a84d4586-...`).

## 2. 이 세션의 변경 (main 반영 완료)

| repo | main HEAD | 핵심 |
|---|---|---|
| ai-platform | `cf6aa9c` | 인제스천 계약 통일·세션 업로드 진입점·crawl 501·kms_sync(tenant/skip)·docforge 비동기 클라이언트·임베딩 동시성·**IDOR 수정** |
| KMS | `de54b4dd` | placement→`document.updated`(도메인 동기화)·죽은 `triggerIngest` 제거 |
| parser(docforge) | `3b44f28` | garbled 오탐 수정·MIXED/born-digital OCR 스킵·타임아웃 상향·`/v1/parse/async` 큐 |

> KMS는 origin push 대기. parser repo에 **제 것이 아닌 선행 미커밋 변경** 3개
> (`document_intelligence.py`, `markdown_assembler.py`, `domain/value_objects.py`) 보존됨 — 누군가의 진행 작업.

### 인제스천 계약 (통일됨)
`POST /api/documents/ingest` 단일 진입점이 두 케이스로 분기:
- **인라인**: `content` 또는 `file_base64` → `ingest` 큐 (HTTP·챗봇 세션 업로드)
- **참조-fetch**: `source_document_id` → `kms_sync` 큐 (KMS, 워커가 파일 fetch)
- 둘 다 없으면 400. → 새 업로더 = 얇은 엔드포인트 + 기존 큐, **코어(`IngestPipeline`) 수정 0**.

## 3. 검증된 것 (라이브 시나리오)

- A: 계약 분기(인라인→ingest / 참조→kms_sync / 빈→400) ✅
- B: 챗봇 세션 업로드 → ingest 큐·`session_id` 메타 태깅·소유권 검증(IDOR 404/소유 202) ✅
- C: crawl → 501 ✅
- D: **KMS 업로드→DB-DAMAGE 배치→단일 문서·올바른 도메인** (`document.created`=skip, `document.updated`=success) ✅
- docforge `/v1/parse/async` 제출+폴링으로 CSV 파싱 완료 ✅

검증은 **소형 CSV** 로만 수행. 대형 PDF(보험 약관 1500p) end-to-end 는 미검증.

## 4. 미완 / 후속

- **DB손해보험 100개 적재·검색 검증** (원 과제): 크롤·파싱 데이터는 `apps/api/scripts/crawl_data/`
  (gitignore, manifest.json 100건)에 있음. 크롤러 `apps/api/scripts/crawl_idbins.py`(미커밋).
  비동기 파이프라인 작동하므로 재개 가능하나 **미완**.
- **rag_search 세션 스코프 검색**: 세션 업로드 문서를 세션 단위로 격리 검색.
  통합지점: `SearchScope`에 `session_id` + `vector_store` 검색 SQL additive 필터 +
  오케스트레이터 scope 주입. (테넌트 격리는 이미 적용.)
- KMS `git push` (origin 미반영).

## 5. ⚠️ 구조적 불안정 요인 (평가 핵심)

1. **docforge가 외부 호스트 서비스 다수에 의존** (OCR 5052·VLM 5053·임베딩 8103).
   이 세션에 **전부 다운 상태였고 행/실패**를 유발. 자동기동·헬스감시 부재. → 단일 서비스 다운이 파이프라인 중단.
2. **docforge 파싱이 무겁고 느림** (페이지당 ~수 초). 비동기 큐로 연결유지는 해소했으나 처리량 한계 잔존.
   비동기 큐가 **단일 인메모리 워커**(`DOCFORGE_WORKERS=1`)라 멀티워커/재시작 시 잡 유실.
3. **사전 버그가 실행 시점에야 드러남** — kms_sync `tenant_id` NULL 위반, create+placement 이중적재 경쟁은
   "그동안 docforge 실패로 도달 못 했던 코드"라 이번에 처음 노출. → **통합 테스트 부재** 신호.
4. **이중 스토어** — KMS `document_contents`(자체) ↔ ai-platform pgvector(RAG)가 별개.
   KMS 문서가 자동으로 RAG에 안 뜸. 동기화는 webhook→kms_sync 단일 경로 의존.
5. **파서 포맷 한계** — `text/markdown` 미지원(pdf/csv/xlsx/xls만). KMS는 md 허용하나 RAG 적재 실패.
6. **`insert_document` 중복 동작** — 동일 file_hash·external_id 로 복수 행 생성 가능(테스트 중 관측).
7. **kms-assistant.yaml 프로필 로드 실패**(`'name'`) — 미해결(다른 프로필 정상).
8. 미커밋 선행 변경(parser)·테스트 잔여물 등 **작업 위생** 이슈가 산재.

## 6. 재현/검증 방법 (요약)

- 단위 테스트: `docker exec aip-api sh -c 'cd /app && pip install -q pytest pytest-asyncio python-multipart && python -m pytest tests/test_async_ingest.py -q'` (16 passed)
- parser 테스트: `cd ~/Desktop/WorkSpace/parser && ./venv/bin/python -m pytest tests/unit/test_page_classifier.py tests/unit/test_text_quality_utils.py -q` (호스트 venv)
- KMS 타입체크: `cd ~/Desktop/WorkSpace/KMS/packages/api && npx tsc --noEmit -p tsconfig.json`
- E2E(시나리오 D): KMS 업로드(`POST :3001/api/documents`, JWT) → 배치(`POST /api/placements`, domainCode=DB-DAMAGE)
  → ai-platform `documents` 에 `external_id`=KMS id, `domain_code`=DB-DAMAGE 단일행 확인.
- 호스트 서비스 기동 필요: `ocr_service.py`(:5052), 임베딩 `:8103`(launchd). VLM(:5053)은 graceful degrade.
