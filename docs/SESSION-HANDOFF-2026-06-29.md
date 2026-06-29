# SESSION HANDOFF — 2026-06-29

> 범위: 채팅 안정화 → 어드민 통합 → 관측성(Phase 3) → LangGraph 이전 → 클린아키텍처 정합 → 하드웨어 모니터링(Phase 4, 진행 중).
> 브랜치: 전부 `main` 반영(프론트=Vercel 자동배포, api/bff=docker 재빌드). 최신 `805c6f7`.
> 비고: main에 병행 트랙(kms-sync `52eeb78`·vlm-enhance `7c06941`)도 공존 — 이 세션 작업 아님.

---

## 1. 완료된 트랙 (커밋)

### A. 어드민 UI 정상화 — 스키마 드리프트 다수
**근본 패턴**: bff TypeORM 엔티티가 **실제 테이블(api/alembic 소유)과 어긋남** → 500/크래시. (백엔드 미구축 아님.)
- `824089e` 3페이지 shape 정합(요청로그·Knowledge·Provider, items/statusCode/documentsBySource 등)
- `548a5c3` profiles 500 — 엔티티 가짜 `mode` 컬럼 제거(테이블엔 없음 → `mode()` 집계 오류)
- `4a3c512` Knowledge 500(Document 엔티티 대량 불일치→실제컬럼) + 요청로그 상세 `ParseUUIDPipe` 제거(id=BIGSERIAL)
- `bad818b` /admin 단일 셸 풀 통합(채팅·아키텍처 메뉴화, `(chat)`/`/architecture` 라우트그룹 제거)
- `2843da3` BFF URL 이중 `/bff/bff/` prefix 정합(9파일)

### B. 채팅 안정화
- `07f43a1` 오케스트레이터 **토픽 전환 감지**(continuation 고정 완화) — 검증: 사주세션 중 보험질문→insurance-qa 라우팅. **단 "자동선택"(chatbot_id=undefined)일 때만 라우팅**. 특정 프로필 선택 시 그대로 사용
- `439440b` 채팅 토큰 스트리밍(`get_chat_model`에 `streaming=True`) + 공통 시간포맷(`lib/format.ts`) + RAG 트레이스 패널
- `a4218e6` 피드백 502 — **bff `AIP_API_URL` 미설정→localhost(자기자신)** → `http://api:8000`(docker-compose.yml). bff `/feedback`는 api로 중계
- `3ae5656` /admin/chat 스크롤 — flexbox `min-h-0`

### C. 관측성 Phase 3 — `ad4d394`
- `api_request_logs`에 `client_ip`·`user_id`·`latency_breakdown`(JSONB) 영속(migration 024) + 프리뷰 4000자
- chat.py 4개 적재 지점(request.client.host·user_ctx.user_id·trace.summary())
- bff 요청로그 상세 노출 + 프론트 상세에 IP·User·**레이어별 처리시간 바차트**

### D. LangGraph 워크플로우 엔진 이전 — `9ffb1d9` (ADR-013)
- `session_store`+state-machine+pause/resume → `AsyncPostgresSaver`+`StateGraph`. **YAML→StateGraph 동적 빌더**로 철학 보존
- feature-flag(AD-1)로 검증 후 **legacy 제거 컷오버**(langgraph 단일). 회귀 0(223 워크플로우/1390 전체)
- 안전망 parity가 실버그 3개 포착·수정

### E. 클린아키텍처 정합 — `e6f7dea` (ADR-014)
- god 분할: `graph_executor` 917→178(+executors/ 모드별 mixin), `vector_store` 859→404(+vector_search.py)
- 레이어 단방향: `ExecutionPlan`→`domain/`, `GraphCache`→`agent/` (agent→router upward 0)
- saju 격리: `services/consumers/saju/`. **mixin 합성(MRO)으로 byte-identical**(본문 무변경). 회귀 0(1390)

---

## 2. 진행 중 — Phase 4 하드웨어/GPU 모니터링

**제약**: api=Docker라 호스트 GPU% 직접 불가. macOS GPU%는 sudo 필요. → MLX 서버(호스트)가 자기 GPU 메모리 노출.

| 부분 | 상태 |
|---|---|
| 호스트 MLX 서버 5개 `/health` 확장 | ✅ **완료·검증** (GPU 8.3/4.6/5GB·CPU·RAM) |
| api `/api/health/hardware` (`c115460`+`805c6f7` ADMIN게이팅) | ✅ 커밋 — **Docker 다운으로 런타임 검증 대기** |
| 프론트 대시보드 하드웨어 패널 + 실시간 그래프 | ⏳ **미착수** (다음) |

**호스트 변경(이 repo 밖, `/Users/eyjs/mlx-servers/`)**:
- 각 서버 디렉토리에 `sysmetrics.py` 배치(`mlx.get_active_memory`/`torch.mps` + `psutil`). 각 서버 **자체 venv**(`<dir>/venv/`; llm-saju는 `llm/venv` 공유)에 `psutil` 설치.
- 각 `main.py` `/health` → `{..., **health_metrics(MODEL_NAME)}`. launchd 라벨: `com.kms.mlx-{reranker:8102,embedding:8103,llm:8104,router:8105}`, `com.joonbi.mlx-saju:8106`.

**다음 작업(Phase 4 마무리)**: Docker 기동 후 ① `/api/health/hardware` 검증(admin JWT) ② 대시보드에 하드웨어 패널(CPU/메모리 게이지 + 서버별 GPU + 클라이언트 폴링 롤링 그래프). frontend→api 직접 호출(SSE처럼; bff는 DB-direct라 라이브 메트릭 부적합).

---

## 3. 남은 과제 (우선순위)

1. **RAG 전체 데이터 감사** (사용자 다음 요청) — **벡터스토어↔KMS 연동 점검**. 확인된 갭: `document_chunks`가 **자동차보험 1217개뿐**, insurance-qa 스코프(건강·실손·자동차·화재)인데 나머지 도메인 데이터 0. (RAG 검색 품질 자체는 정상 — 코사인 0.65, "0.016"은 RRF 점수. ADR/리뷰 참조.)
2. **Phase 4 프론트 마무리** (위).
3. **facts 테이블 비어있음** → fact_lookup 항상 "실패"(에러 아님, 정형팩트 미적재).
4. **non-ADMIN 403 채팅** — bff JWT에 `allowed_profiles` 클레임 누락 → 비관리자 strict mode 403.
5. **클린아키텍처 후속** — executor mixin의 `self` 계약을 Protocol로 명시(타입안전), `tools/internal/`의 saju 전용 모듈(`saju_report_*`·`saju_*_prompts`) consumer 격리.
6. **채팅 지연** — 로컬 9B agentic ~50s. 근본 한계(스트리밍으로 체감 개선).

---

## 4. 운영 / 함정 노트

- **배포**: api/bff 수정 = `docker compose up -d --build <svc>`. frontend = main push → Vercel. MLX 서버 = launchd `launchctl kickstart -k gui/$(id -u)/<label>`.
- **Docker 데몬**: 환경 재시작 시 내려감. `open -a "Docker Desktop"`(정식 앱명; `open -a Docker`는 VM 미기동) 후 ~1분. `docker ps`는 데몬 미응답 시 무한 hang → `perl -e 'alarm 8; exec @ARGV' docker ps`로 바운드.
- **MLX 서버**: 호스트 launchd, 서버마다 **별도 venv**. 행 걸리면 `lsof -ti:PORT` 점유 프로세스 kill 후 kickstart.
- **bff↔api 경계**: bff는 **DB 직접**(api HTTP 호출 금지)가 원칙. 단 `/feedback`은 예외적으로 api 중계(AIP_API_URL).
- **alembic**: DB가 `022`에 머물러 있던 것(023·024 실제 스키마는 반영됨)을 **`024`로 스탬프 정정**. initdb 베이스라인이 스키마를 앞서게 만든 패턴.
- **파이프라인(/go)**: `.pipeline/requirement.md`(SSOT) → orchestrator. **오케스트레이터가 백그라운드+대기로 턴을 끝내 정체되는 경향** → "implementor 포그라운드 구동 + 한 번에 끝까지" 명시하면 해결(클린아키텍처 트랙에서 입증). `.pipeline/`은 gitignore.
- **자동 라우팅**: 채팅 멀티라우팅은 드롭다운 **"자동 선택"**(chatbot_id 미전송)에서만 동작.

---

## 5. 핵심 문서
- ADR: `docs/adr/adr-013`(LangGraph 이전)·`adr-014`(클린아키텍처)
- 앱별 컨텍스트: `apps/{api,bff,frontend}/CLAUDE.md` (자급자족) — api는 새 구조(executors/·vector_search·domain/execution_plan·consumers/saju) 반영됨
- 파이프라인 산출물: `.pipeline/{REPORT.md, requirement.*.md, reviews/}` (로컬, gitignore)
