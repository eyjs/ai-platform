# RAG 파이프라인 지연 분석 & 개선 방향 (2026-07-07)

> 계기: 채팅 트레이스에서 `graph_stream` 55.0s / `rag_search` 8.3s / `fact_lookup` 0ms 관측 →
> "graph_stream에 장애가 있는 것 같다"는 의심. 코드·직접 측정으로 검증한 결과와 개선안.

## TL;DR

- **`graph_stream`은 장애 지점이 아니라 "전체 스트림 실행시간"을 재는 래퍼 트레이스 노드다** (`graph_executor.py:178`). 55s = 그 안에서 벌어진 모든 작업의 합.
- **[실측 갱신 2026-07-08]** 관측성(P0)을 적용해 단계별 시간을 실측한 결과, **병목은 생성이 아니라 검색(retrieval)이었다.** 아래 §0 참조. 초기 가설("생성이 지배적")은 부분적으로만 맞았다 — 컨텍스트가 있을 때만 성립하고, 실제 관측된 질의에서는 rag_search 콜드스타트 + 헛도는 재시도가 지배적이었다.
- 지연을 배로 키우는 구조적 요인 두 가지: **Adaptive Retry 루프**(`rewrite_query → execute_tools` 재검색)와 **Guardrail Regeneration 루프**(`regenerate`). 둘 다 느린 로컬 모델/검색을 한 번 더 태운다.
- `fact_lookup` 0ms는 정상 — 초기화로 `facts` 테이블이 비어 즉시 반환.
- 모델 서버(8102 reranker / 8104·8105·8106 LLM)는 전부 헬스 정상. 서버 다운/행 아님.

---

## 0. 실측 검증 (2026-07-08) — 관측성 적용 후

P0(모든 단계 실시간 트레이스+로그)를 구현·배포하고 KMS 챗으로 실제 질의한 결과:

```
tool_execution start
  rag_search    20,409ms   ← 첫 검색: 콜드스타트(임베더/리랭커 초기 로드) 의심
  fact_lookup        0ms
evaluate_results  retry_count=1     ← 결과 0건 → 불충분 판정
rewrite_query     retry attempt=1   ← ★Adaptive Retry 발화(실측 확인)
  rag_search     2,278ms   ← 재검색(웜: 2.3s로 급감 → 첫 호출 18s가 콜드스타트임을 방증)
evaluate_results  retry_count=2     ← 여전히 0건 (재시도가 헛수고)
tool_execution end  32,717ms  results=0  retries=1
generation start  context_chunks=0
generation end    3,350ms  ttft=3,341ms   ← 생성은 짧음(컨텍스트 0 → 짧은 답변)
guardrail end     0.1ms
```

**해석**
1. **검색이 지배적(32.7s)이고 생성은 3.3s에 불과했다.** "생성이 병목"이라는 초기 가설은 이 질의에선 틀렸다. (단, 컨텍스트가 실제로 채워지면 생성이 15 tok/s로 다시 커진다 — §3-2. 두 병목은 상황에 따라 교대한다.)
2. **rag_search 첫 호출 20s = 콜드스타트.** 두 번째 호출이 2.3s로 급감 → 첫 호출에 임베더/리랭커 모델 로드 등 워밍업 ~18s가 섞였다. (서버 헬스는 즉답이지만 실제 추론 경로는 첫 호출에 초기화 비용 발생.)
3. **Adaptive Retry가 결과 0건에도 발화해 헛돈다.** `evaluate_results`가 score≥0.4 결과가 없으면 retry_count++ → rewrite_query → 재검색. 그런데 KB가 비면 재검색해도 0건이라 **순수 낭비**(rag_search 왕복 추가). §5 P1-4의 "빈 결과면 재시도 스킵"이 정확히 이 케이스를 막는다.
4. `chunks_per_s` 값은 짧은/버스트 응답에서 노이즈가 크다(69청크가 ttft 이후 9ms에 몰림) → 신뢰 지표는 **ttft**. 서버가 토큰을 버스트로 보내는 정황도 관측됨(별도 UX 확인거리).

> 이 실측은 문서 1개짜리 빈 KB 상태에서 얻은 것으로, §4의 "상황적 특수성" 경고와 일치한다. 정상 부하(문서 다수)에서는 검색 결과가 채워져 재시도가 줄고 생성 비중이 커질 것이다. **핵심 성과: 이제 어느 쪽이 병목인지 매 질의마다 트레이스로 즉시 판별 가능하다.**

---

## 1. 측정된 사실

| 항목 | 값 | 판정 |
|------|-----|------|
| `graph_stream` | 55.0s | 래퍼(전체 합). 단일 컴포넌트 아님 |
| `rag_search` | 8.3s | 21청크치곤 과다 — 리랭커 주도 추정 |
| `fact_lookup` | 0ms | 정상 (facts 테이블 empty) |
| main LLM 생성속도 | **~15 tok/s** | 85토큰/5.6s 직접 측정. 느림 |
| main LLM 모델 | `mlx-community/Qwen3.5-9B-4bit` | reasoning 모델(토큰 인플레) |
| 모델 서버 헬스 | 8102/8104/8105/8106 전부 200 즉답 | 서버 정상 |

> main LLM `host.docker.internal:8106`, router LLM `8105`, report LLM `8104`, reranker `8102`, 임베딩 서버 별도. 전부 host MLX(GPU).

---

## 2. 결정론적 RAG 그래프 구조 (`agent/graphs.py`)

```
route_by_rag (진입)
├─ direct_generate ──────────────────────────────► END      (인사/잡담: RAG 스킵)
└─ plan_execution ─► execute_tools ─► [graph_enrich?] ─► evaluate_results
       (router LLM)   (rag_search,      (KMS 그래프         │
                       fact_lookup)      컨텍스트 보강)      │
                                                            ├─(불충분)─► rewrite_query ─► execute_tools   ← ★재시도 루프
                                                            │             (LLM)          (rag_search 재실행!)
                                                            └─(충분)───► generate_with_context ─► run_guardrails
                                                                          (main LLM, 병목)        │
                                                                                                  ├─(위반)─► regenerate ─► run_guardrails  ← ★재생성 루프
                                                                                                  └─(통과)─► build_response ─► END
```

한 번의 질의에서 LLM을 최대 **여러 번** 호출한다: ① plan_execution(router) ② rewrite_query(재시도 시) ③ generate_with_context(main) ④ regenerate(가드레일 실패 시). 각 호출이 느린 로컬 모델을 탄다.

관련: `agent/graphs.py:90-142`(엣지), `agent/nodes.py`(노드 구현), `agent/graph_executor.py:148-178`(스트리밍 래퍼).

---

## 3. 병목 원인 분석

### 3-1. `graph_stream` = 관측상의 착시 (구조)
`graph_executor.py:178`이 `execute_stream()` 전체를 감싸 `total_ms`를 `graph_stream` 노드로 기록한다. 자식 작업(도구·생성·재시도) 시간이 부모에 합산되므로, 생성이 느리면 "graph_stream이 55s"로 보인다. **generate_with_context 노드가 트레이스 UI에 노출되지 않으면**(사용자가 본 화면엔 rag_search·fact_lookup·graph_stream만 있었음) 병목이 graph_stream인 것처럼 오인된다 → **관측성 갭이 진짜 문제**.

### 3-2. 로컬 LLM 생성 속도 (지배적 요인)
`Qwen3.5-9B-4bit` @ ~15 tok/s. 55s 중 rag_search(8.3s)+fact_lookup(0s)를 뺀 ~46s의 대부분이 `generate_with_context`(+가능하면 재시도). reasoning 모델이라 `<think>` 토큰까지 뽑으면 답변당 수백~1천 토큰 → 30~60s. **이게 지연의 구조적 하한.**

### 3-3. 재시도 루프 두 개 (증폭 요인)
- **Adaptive Retry**(`evaluate_results → rewrite_query → execute_tools`): 검색 결과가 "불충분" 판정되면 쿼리 재작성(LLM 1회) + **rag_search 재실행(+8s)** + 재평가. 현재 벡터스토어에 문서 1개뿐이라 대부분 질의가 불충분 판정 → 루프 진입 가능성 높음.
- **Guardrail Regeneration**(`run_guardrails → regenerate`): 가드레일 위반 시 main LLM 재생성(+30~40s).

### 3-4. `rag_search` 8.3s (2차 병목)
`tools/internal/rag_search.py`가 `reranker_pipeline.rerank_3tier`(3단계 리랭킹)를 호출. 청크 21개인데 8.3s면 임베딩 왕복 + 3-tier 리랭커가 주도. 문서가 늘면 더 커질 수 있는 항목.

### 3-5. `fact_lookup` 0ms
초기화로 `facts` empty → 즉시 반환. 정상. (문서가 쌓이고 fact 추출이 돌면 값이 생김)

---

## 4. 근본 원인 정리

| # | 원인 | 성격 | 영향 |
|---|------|------|------|
| A | main LLM ~15 tok/s (로컬 9B reasoning) | 구조적 | 생성 30~46s (지배적) |
| B | Adaptive Retry가 빈약한 KB에서 자주 발화 | 상황적(현재 문서 1개) | rag_search·생성 중복 → 배증 |
| C | Guardrail Regeneration | 상황적 | 위반 시 재생성 배증 |
| D | rag_search 리랭킹 8.3s | 구조적 | 검색 단독 지연 |
| E | 생성 노드가 트레이스에 안 보임 | 관측성 | graph_stream 오인의 원인 |

> **주의(현재 상태 특수성)**: 방금 KMS↔ai-platform 초기화로 벡터스토어에 문서가 1개뿐이다. 검색 품질이 낮아 재시도 경로(B)가 과하게 타므로, **지금 측정한 55s는 정상 부하 대비 과대평가**일 수 있다. 다만 A(LLM 속도)와 E(관측성)는 KB 크기와 무관한 구조적 문제.

---

## 5. 개선 방향

### P0 — 관측성부터 (오진 방지)
1. **생성 시간을 트레이스에 노출**: `generate_with_context`/`regenerate`/`plan_execution`/`rewrite_query` 노드 duration을 채팅 트레이스 UI에 항상 표기. graph_stream(총합)과 자식 노드 합을 나란히 보여 "어디서 시간이 갔나"가 한눈에 보이게. (KMS `ChatTracePanel` + ai-platform 트레이스 emit 양쪽)
2. **prefill/decode 분리 계측**: main LLM 호출에 time-to-first-token과 tok/s를 로깅(대용량 컨텍스트 prefill 비용 가시화).

### P1 — 지연 실질 감소
3. **생성 모델 선택 재검토**: 답변 생성용으로 (a) 비-reasoning 또는 reasoning-off 모드, (b) 더 작은/양자화 강한 모델, (c) 속도 우선 라우팅. reasoning 토큰 억제만으로도 체감 큰 폭 개선 예상.
4. **재시도 루프에 상한/조건 강화**: Adaptive Retry 최대 횟수 캡 + "KB가 비었거나 후보 0개면 재시도 스킵"(빈 KB에서 무의미한 재검색·재작성 방지). `route_by_evaluation` 조건에 후보 수/스코어 임계 추가.
5. **가드레일 재생성 억제**: regenerate는 1회로 제한돼 있으나, 위반 빈도·사유를 로깅해 불필요한 재생성이 얼마나 잦은지 측정 후 임계 조정.

### P2 — 검색 경로 최적화
6. **rag_search 8.3s 분해**: 임베딩 왕복 vs `rerank_3tier` 각각의 소요를 계측. 청크 후보가 적을 땐 3-tier를 1~2tier로 축소하거나 스킵하는 적응형 리랭킹.
7. **graph_enrich 비용 확인**: `has_graph` 경로에서 KMS 그래프 컨텍스트 조회(`agent/graph_enrich.py`)의 왕복 지연 계측 — 문서/관계가 늘면 커질 수 있음.

### 정상 부하 재측정
8. 문서가 어느 정도 쌓인 뒤 동일 질의를 재측정해 B(재시도)의 기여분을 분리. 현재 1-문서 상태 수치는 기준선으로 부적합.

---

## 6. 오해 정정 요약

- **"graph_stream 장애"** → 아니오. graph_stream은 전체 실행시간 래퍼(`graph_executor.py:178`)이며, 실제 시간은 그 안의 **느린 로컬 LLM 생성 + (빈약한 KB로 인한) 재시도 루프**가 부모에 합산된 것. 실패한 서브시스템이 아니라 관측성 갭 + 로컬 추론 속도 문제.

---

*측정: main LLM `/v1/chat/completions` 직접 호출(85tok/5.6s), 모델 서버 헬스 4종, `agent/graphs.py`·`graph_executor.py`·`tools/internal/rag_search.py` 코드 검토. 채팅 요청의 노드별 로그는 미포착 → P0-1로 상시 노출 필요.*
