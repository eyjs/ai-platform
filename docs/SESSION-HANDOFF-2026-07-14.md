# SESSION HANDOFF — 2026-07-13 ~ 07-14

> 범위: **RAG 파이프라인 전수 검토 → 결함 수정 → 신뢰성·관측성·UX 강화**. 사용자 실사용("첫 질문 무응답")에서 출발해 프론트 SSE 파서 → 관측 공백 → PG 엔진 회귀 → 비교 질문 체인 → 검색 비결정성 → 진행 상태 UX까지 계단식으로 근본 수정.
> 브랜치: 전부 `main` 푸시·배포 완료 (`3e6fe75` → `52af093`, 15커밋). api 컨테이너·Vercel 프론트 모두 최신.
> 테스트: api **1,518 passed** / frontend **75 passed**.

## 1. 근본 원인 규명 — "첫 질문은 답변 안 함" (`3e6fe75`)

- **프론트 SSE 파서가 CRLF 미대응**: sse-starlette는 이벤트를 `\r\n` 종결하는데 파서가 `\n` split + `line===''` 경계 판정 → 이벤트 경계를 영원히 못 만나고, 청크가 `\n`으로 끝나는 순간의 **마지막 이벤트 하나만** 우연히 살아남음. 토큰 스트리밍 전멸, 답변은 done 이벤트가 파싱될 때만 한 방 표시.
- 첫 질문(콜드, TTFT 40s+)은 무표시 → 사용자가 중지(스트리밍 중 전송 버튼=중지)/포기 → `isStreaming:true` 빈 말풍선 영구 고착.
- 수정: 증분 파서(`parseSSEBuffer`, `\r\n|\n|\r` 허용, 완결 이벤트만 소비) + done 미수신 마감(onIncomplete) + 로드 시 잔존 스트리밍 정규화.
- 진단 요령(재발 시): `conversation_sessions.turns`(서버) ↔ localStorage `aip-chat-sessions`(클라) 대조 — 서버에 턴이 있는데 클라가 비면 전달 구간.

## 2. 관측 공백 해소 (`1548110`, `65c070a`, `ee2284e`)

- supervisor 경로 `api_request_logs` 미기록 → 기록(500 포함) + `latency_breakdown`(RequestTrace를 supervise에 전달).
- 비스트리밍 `/chat` `latency_ms` 항상 0(latency_timer 컨텍스트 종료 순서 버그) → enqueue 시점 직접 계산.
- 비스트리밍 생성·가드레일 트레이스 노드 부재(graph_execute 총합만 보임 — "2m 20s 미스터리") → `generate_with_context`/`run_guardrails` 노드 기록.
- supervisor 스트림에 위임 서브의 trace 중계 + 연결 즉시 `trace(supervisor start)` — 첫 토큰까지 무신호 제거.

## 3. PostgreSQL 16.12 회귀 (인프라)

- **CVE-2026-2006 여파 회귀**: 압축 저장(pglz/lz4, 인라인) 멀티바이트 text의 `SUBSTRING/left`가 "invalid byte sequence" 오류(16.11 정상, 16.13 수정). 당시 코퍼스 26% 청크가 metadata_only 검색(`SUBSTRING(content,1,250)`)에서 지뢰.
- 조치: `|| ''` full-detoast 우회(`607a8bd`, 유지 무해) + **postgres 16.14 업그레이드 완료**(ai-platform + KMS, 데이터 무손실, 전수 스윕 0 실패). saju/joonbi는 15.15로 무관.

## 4. 응답 길이 2단 방어 (`febde0e`)

- "핵심만" 질문에 9,506자/2분+ 생성(로컬 MLX) → ① L3 간결 신호(핵심만|간단히|짧게|요약 등, 결정론 패턴) → volatile 지시 주입, ② `Profile.max_output_tokens` 신규 배선(로더→plan→4개 프로바이더 per-call max_tokens). insurance-qa 2048.
- 겸사: 스트리밍 경로가 `volatile_system_prompt`(날짜 grounding)를 유실하던 버그 수정 — cacheable/volatile 분리 전달로 계약 통일.
- supervisor planner에 "형식 요구를 subquery에 보존" 규칙(위임 재작성에서 "핵심만" 유실 방지).

## 5. JWT 만료 처리 3겹 (`313111f`)

- 13분 인터벌 갱신은 탭 잠들면 미동작 → 채팅 401이 일반 "연결 끊김"으로 뭉개짐.
- ① 전송 직전 exp 검사·선제 갱신(`ensureFreshAccessToken`), ② 401/403 `auth_error` 이벤트 → 갱신+같은 요청 1회 자동 재시도, ③ 갱신 실패 시 /login. 종단 이벤트 후 onIncomplete 덮어쓰기 버그도 수정(sawTerminal).

## 6. 비교 질문 체인 — 오답 원인 4개 (`32142ce`,`16c7240`,`24064f2`,`213b10e`)

"A랑 B 비교"가 "B 문서 없음" 오답이 되는 사슬을 전부 절단:
1. 비교 감지가 `if history` 게이트 안 + 커스텀 인텐트가 STANDALONE 강제 → `_detect_comparison` 최우선·직교 승격(커스텀 라벨은 보존).
2. 플래너가 `max_vector_chunks=2`로 검색 굶김 → 전략 매트릭스 하한 가드(늘리기만 허용).
3. 리랭커 정원을 한 상품이 독식 → 문서 다양성 캡(top_k≥8, 문서당 max(2, top_k//3), 총량 보존 백필).
4. 플래너가 분리 검색에 fact_lookup 오선택 → 예시를 완전한 step 객체로(소형 로컬 LLM은 추상 지시 무시 — 실측 교훈).
- `<br>` 미렌더링도 함께: rehype-raw + rehype-sanitize(스크립트 차단, highlight용 code className 허용).

## 7. 검색 비결정성 근본 해결 (`d9b6e02`) ★

"같은 질문이 시도마다 성패 왕복"(정답 청크 fused 0.646이 정원 5 밖 6위) 제거:
1. **정원 마진**: STANDALONE/ANSWER_BASED top_k 5→8 (SAME_DOC은 문서 고정이라 3 유지).
2. **동점 정렬 고정**: fused 동점 시 chunk_id tie-break.
3. **무답변 확장 재시도**: 답변이 "확인이 필요합니다" 류 정형 문구(locale `no_answer_markers`)면 정원 2배(상한 16)로 검색→생성 1회 재실행. 스트림은 `trace(widen_retry)` + `replace("")` 후 재스트리밍. **"없음" 결론은 반드시 확장 검색까지 거친 뒤라는 보증.** 얼버무린 부분 답변도 트리거돼 품질 개선(실측). 발동 시 소요 75~85초(미발동 36초).
- 검증: 문제 질문 7연속(로컬 3 + 프록시 4) 무답변 0회.

## 8. 진행 상태 UX (`d469acd`) + 표기 규칙 (`52af093`)

- 말풍선이 답변 전 **현재 단계를 회색 글씨로 고지**(`lib/trace-status.ts` 매핑): 분석→검색→"N개 구간 검토"→작성→검증, 확장 재시도 시 "찾지 못해 검색 범위를 넓혀 다시 확인하는 중..." + 이후 단계에 재검색 맥락 유지. `role=status`/`aria-live`.
- rag_instruction 표기 규칙: 출처 일반 텍스트(가짜 마크다운 링크 금지), 한국어 유지, 약관-요약서 상충 시 약관 수치 우선. 라이브 검증됨.

## 9. 남은 갭 / 다음 후보

- **확장 재시도 프로필 옵션화**: 속도 우선 프로필용 off 스위치 (현재 전역 on).
- **재시도 발동률 모니터**: `no_answer_widen_retry` 로그 집계 — 발동률이 높으면 검색 품질 신호.
- ~~질문 기반 메타필터(P2)~~ **완료(`0d8b6dc`)**: 깔때기 1단계 — 코퍼스 문서명에서 유도한 별칭(결정론 매칭, 과반 커버 토큰 자동 제외, TTL 5분)으로 검색 범위 축소. 라이브 실증: 상품 질의 후보 풀이 해당 상품 3문서로 정확히 축소, 무엔티티 질의는 무필터, 빈손 시 무필터 폴백. `trace_detail.filter.entity_filter` 관측.
- ~~KMS 그래프 실데이터~~ 완료(07-13, 13관계). 남은 갭: docforge 네이티브 AST / router_model KNOWN GAP.
- 사소: 실손 상품요약서 표 파싱은 정상 확인(어제 "표 유실" 판정은 검증 정규식 오류였음 — 부재 판정은 표기 변형 여러 개로 검증할 것). 로컬 8B 모델의 산발적 영어 혼입은 프롬프트 규칙으로 감소했으나 완전 제거는 아님(모델 한계).

## 10. 운영 메모

- 테스트 JWT: `AIP_JWT_SECRET`(docker env)로 HS256 self-mint, exp 2h — 만료 시 401 "토큰이 만료되었습니다".
- api 배포: `docker compose build api && docker compose up -d api`. 컨테이너 재생성 시 docker logs 유실.
- SSE 디버깅: 이벤트는 `\r\n` 종결 — 파서 검증 시 필수 전제.
