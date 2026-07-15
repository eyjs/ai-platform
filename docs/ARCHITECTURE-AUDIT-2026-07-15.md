# 아키텍처 전수 진단 — 2026-07-15

> 방법: 병렬 3축 심층 감사(동시성·용량 / 의미 라우팅 / 배선·경계) 후 교차 종합.
> 대상: apps/api 29,104 LOC 중심 + frontend/bff 계약면. 전부 파일:줄 근거 기반.
> 요약 판정: **레이어 경계는 건전**(역참조 0, Tool 봇식별 0, Router 하드코딩 0). 위험은 세 군데 집중 —
> ① **용량: 100명 동시 불가(절벽 붕괴형)**, ② **라우팅: 결정론적으로 틀린 곳에 새는 오탐/폴백**, ③ **죽은 설정·계약 분열**.

---

## A. 100명 동시 사용 판정: 불가 (현행 구성)

실질 동시 활성 한계 **~15–30건** 추정. 완만한 열화가 아니라 절벽형 붕괴.

> **전제 정정(종합자)**: 감사가 base compose(`AIP_PROVIDER_MODE: anthropic`)를 봤으나 **실제 런타임은
> `docker-compose.override.yml`이 development(로컬 MLX 8106)로 덮는다**. 즉 답변 생성까지 단일 GPU
> 직렬(25~70초/건) — 판정은 보고서보다 더 단호하게 "불가". (상용 스왑 스위치 `AIP_MAIN_LLM_BACKEND`를
> 켜면 생성 병목은 해소되고 아래 병목들만 남는다.)

병목 순위:

| # | 병목 | 근거 | 100명 증상 |
|---|---|---|---|
| 1 | **전역 동시성 상한/백프레셔 부재** | `helpers.py:73-82` increment_active는 카운터일 뿐, `config.py:115 max_concurrent_agents=50`은 **소비 0건 죽은 설정** | 100요청 전부 파이프라인 진입 → 전 구간 동시 타임아웃 |
| 2 | **로컬 MLX 검색 직렬 + 타임아웃 사슬** | 질의 임베딩에도 적재용 120s×3retry 적용(`config.py:111`, `http_embedding.py:38`) | 임베딩 행 시 서킷 열리기 전 요청들이 120~360초씩 적체 (실사고 재현 경로) |
| 3 | **PG 풀 20 + acquire 무한대기** | `config.py:117 pg_pool_max=20`, acquire 타임아웃 없음, RLS로 acquire당 왕복 추가 | 20슬롯 소진 후 80요청 무한 대기, bff·worker 합산 시 max_connections 근접 |
| 4 | **uvicorn 단일 프로세스** | compose·Dockerfile 모두 `--workers` 없음 | 루프 블로킹 1건 = 전원 정지, 멀티코어 0 |
| 5 | **레이트리밋은 속도만 제한** + fire-and-forget 누적 | `rate_limiter.py:20-44`(사용자별 60/분), `chat.py:348,609` create_task 무상한 | 100명 각자 버킷으로 전원 통과; 메모리 추출 태스크 누적 |

## B. 의미 라우팅 취약성: "엄청 취약" 부분 타당

병증 규정: **비결정성이 아니라**(라우팅 LLM 전부 temperature=0 그리디) "값싼 부분문자열 규칙과 검증
없는 폴백이 **확정적으로** 오도". 재현 쉽고 고치기도 상대적으로 쉬움.

| # | 취약점 | 근거 | 오분류 예시 |
|---|---|---|---|
| V1 🔴 | **sticky 하이재킹** — 미완료 워크플로우 발견 시 TTL·관련성 검증 없이 새 질문 강제 투입, decompose 통째 우회 | `supervisor/graph.py:168-192` | 사주 워크플로우 방치 → "자동차보험 절차?"가 사주에 갇힘 |
| V2 🔴 | **decompose 폴백 = candidate[0] 맹목 위임** — 8B JSON 파손 1회면 임의 프로필로 | `planner_llm.py:139-144` | 과거 "보험→사주" 실사고의 서식지 |
| V3 🔴 | **인사 패턴 오탐 → RAG 통째 드롭** — 감사 패턴 무앵커 + greeting엔 도메인 탈출구 없음(system_meta엔 있음, 비대칭) | `ko.yaml:127`, `intent_classifier.py:99-112` | "고맙게도 이 특약이 보장되나요?"(≤30자) → GREETING |
| V4 🟠 | 커스텀 인텐트 substring + **1글자 패턴 "건"** | `intent_classifier.py:89-94`, `flowsns-ops.yaml:106` | "이 조건이..." → TASK 오태깅 |
| V5 🟠 | system_meta "기능/상태" substring — 도메인 가드가 문자 그대로 "보험" 타이핑 시만 작동 | `ko.yaml:132-134` | "이 특약 기능이 뭐죠?" → 봇 소개 응답 |
| V6 🟠 | 비교 마커("차이"/"vs") 최우선 승격의 과잉 트리거 | `intent_classifier.py:52-54` | "차이나타운 화재보험..." → CROSS_DOC |
| V7 🟡 | LLM 의도 폴백이 `history` 없으면 미작동 — **첫 턴 의미 판단 공백** | `intent_classifier.py:71` | 첫 질문은 키워드 히트 아니면 무조건 STANDALONE |

건전한 부분(과장 구간): SemanticClassifier(threshold+NONE+정확일치), ModeSelector 폴백 사슬, ContextResolver 2-tier.

## C. 배선·경계: 경계 건전, 이음매 5건

- ✅ 레이어 단방향 유지 (역참조 import 0), Tool 봇식별 0, Router 하드코딩 0, JWT RS256 정합, bff 로그 중복쓰기 없음, 임베딩 실사고(타임아웃+서킷)는 봉합 확인.
- **[HIGH] job_* 튜닝 설정 6개 전부 미배선** (`config.py:123-128` ↔ `worker_main.py:149-167` 하드코딩) — env를 바꿔도 무효.
- **[MED] SSE done 페이로드 계약 분열** — 방출 5곳 필드 집합 제각각. 레거시 경로는 `sources` 미보장(출처 배지 조용히 소실 가능), 캐시히트/폴백 경로는 sources·score 둘 다 없음. 역방향 고아 필드(confidence/traversal_path 등)도 존재.
- **[MED] 서킷 불균형**: docforge(폴링 최대 5400s)·KMS(타임아웃 10s 하드코딩)·flowsns 서킷 미보호 — 다음 "무한대기" 후보는 docforge.
- **[LOW] 죽은 필드/설정**: `plan.external_context`(주석이 오배선 함정), `plan.workflow_step`, `state.latency_ms`, config의 `max_concurrent_agents`·`orchestrator_provider`·`fallback_profile_id`·`greeting_max_length`·`response_policy`·`llm_system_prefix`.

---

## 우선순위 로드맵

### P0 — 정확성·생존 (즉시)
1. **전역 동시성 게이트 실배선**: `max_concurrent_agents`를 Semaphore로 강제, 초과 시 503+Retry-After (대기 아닌 유계 거부). [용량 1위 + 죽은설정 동시 해소]
2. **질의 임베딩 타임아웃 분리**: 질의 5~10s/2retry, 적재 120s 유지. [용량 2위]
3. **PG acquire 타임아웃 + 풀 40~60** (api+bff+worker 합 < max_connections). [용량 3위]
4. **V1 sticky 이중 가드**: 세션 TTL + 새 질문↔워크플로우 도메인 임베딩 유사도 임계. [라우팅 최상위]
5. **V2 폴백 교정**: candidate[0] → 일반/폴백 프로필로 (오도메인 → 무해한 일반 답변으로 강등).
6. **V3 봉합**: 감사 패턴 `^` 앵커 + greeting에 domain_scopes 탈출구 복제(3줄).

### P1 — 구조 개선
7. substring 매칭 → **토큰 경계 매칭 + 패턴 최소 2글자** (커스텀·비교·하이브리드 공통, `ko.yaml tokenize` 재사용). [V4·V5·V6]
8. **decompose 앞 임베딩 사전필터**: 질문↔프로필 description 유사도 top-k만 8B에, top-1 임계 미달 시 폴백 — "신호로 좁히고 LLM은 좁혀진 것만". [V2 근본 + 프로필 증가 대비]
9. **SSE done 계약 단일화**: `build_done_payload()` 일원화 + 프론트 타입 계약 테스트.
10. **죽은 설정 정리 + 메타 테스트**: "Settings/plan 모든 필드 소비처 ≥1" CI 검증 — 이 부류 재발 차단.
11. **서킷 확장**: docforge·KMS에 기존 `retry_async(breaker=...)` 재사용, KMS 타임아웃 config 승격.
12. uvicorn `--workers 2~4` (프로세스별 상태 곱셈 주의: 서킷·캐시·카운터·풀).

### P2 — 위생
13. V7 첫 턴 의미 폴백(임베딩 이진 신호), 죽은 필드 제거(`external_context` 주석 정정 포함), fire-and-forget 메모리 추출 유계화, 비스트리밍 경로 keepalive.

---

부록 — 외부 의존 회복력 매트릭스:

| 의존 | timeout | circuit | health | fallback |
|---|:--:|:--:|:--:|:--:|
| 임베딩(MLX) | ✅ (질의용 과대) | ✅ | △ | — |
| 리랭커(MLX) | ✅ 30s | ✅ | ✗ | 리랭크 생략 |
| 라우터/메인 LLM(MLX) | ✅ 120s | ✅ | ✅ | Ollama |
| docforge | ✅ (폴링 5400s) | ❌ | ✅ | ParseError |
| KMS | ⚠️ 10s 하드코딩 | ❌ | ✅ LinkMonitor | ✅ Null 클라이언트 |
| flowsns | ✅ 15s | ❌ | ✗ | 504 |
