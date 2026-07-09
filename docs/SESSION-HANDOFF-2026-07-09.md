# SESSION HANDOFF — 2026-07-09

> 범위: Supervisor Agent P1 트랙 완결 — P1-0 StateGraph 전환 → P1-1~P1-4 구현 → 라이브 실측.
> 브랜치: 전부 `main` 로컬 커밋(`2a3c830` → `c89cd03` → `51d903b`). **푸시 안 함.**
> api 컨테이너는 새 코드로 리빌드 완료·기본 플래그로 가동 중(P1 opt-in 플래그 off).
> **Phase 3(오케스트레이터 → Supervisor 통합)은 사용자 결정으로 대기(보류).**

---

## 1. 완료된 작업 (커밋)

### A. P1-0 — 명령형 위임 루프 → LangGraph StateGraph (`2a3c830`)
- `supervisor/graph.py`(신규): resolve_scope → detect_sticky → (sticky_delegate | decompose) → delegate → collect → finalize
- `supervisor/state.py`(신규): SupervisorState TypedDict. `supervisor.py`는 컴파일 그래프 파사드로 축소(공개 API 불변)
- P0 계약 전부 승계(hub 강제·단일 관문·캡·sticky·핸드오프 passthrough). 기존 테스트 무수정 통과가 회귀 기준

### B. P1-1~P1-4 (`c89cd03`)
- **P1-2 병렬 위임**: dispatch 라우터의 `Send` fan-out. 인가·예산 소비 = dispatch 단일 지점(정적 스캔으로 `Send(` 1곳 강제). results는 reducer 채널 — 계획 순서 결정적
- **P1-1 adaptive replan**: collect 후 조건부 엣지 → replan 노드. 총 위임 캡 공유, 동일 프로파일 재위임 코드 차단, `max_replan_rounds` 상한. **opt-in `AIP_SUPERVISOR_ADAPTIVE_REPLAN`(기본 off — 오라우팅 위험+턴당 LLM 호출 추가)**
- **P1-3 위임 트레이스**: 위임별 {profile, subquery, reason, ok, error, latency_ms, round} → 응답 `TraceInfo(mode=supervisor).router_decision.delegations`
- **P1-4 검토 게이트**: 판정(pass/fail)만, 재생성 금지. reject는 ok=False 강등 → 기존 degrade 종합 재사용. fail-open. **opt-in `AIP_SUPERVISOR_REVIEW_GATE`(기본 off)**
- 테스트: supervisor 92 / 전체 1516 passed. 신규 `test_supervisor_graph.py`(토폴로지·병렬성), `test_supervisor_replan_review.py`

### C. 라이브 실측 (로컬 MLX, port 8020, admin JWT)
| 시나리오 | 결과 | 실측 |
|---|---|---|
| A. 멀티도메인(보험+KMS) | 2위임 병렬 — 벽시계=max(10.8s), 순차면 15.5s | 총 101s |
| B. 워크플로우 핸드오프(사주) | 단일 위임 handoff, 페르소나 질문 무훼손 passthrough | 54.7s |
| C. sticky 2턴차 | decompose 우회, 워크플로우 다음 단계 진행(끊김 재발 없음) | 2.0s |
| D. deny(VIEWER) | 위임 0건, LLM 호출 전 차단 | 10ms |
| E. replan+review on | review 판정 트레이스 노출, replan "충분"→빈 계획 종료 | 17.2s |

---

## 2. 조사 중 발견한 잔여 이슈 (supervisor 외부)

1. **로컬 9B(8106) synthesize 반복 루프** — 긴 종합에서 "자기부담금"×수백 토큰 반복. MLX repetition penalty 설정 필요
2. **RAG chunks 0** — 자동차보험 코퍼스(1217청크) 존재하는데 "자기부담금" 질의 0건. 기존 RAG 데이터 감사 과제와 연결
3. **4B 리뷰어 note 모순** — passed=true인데 note는 부정 서술. 판정은 bool만 신뢰. 게이트 실전 투입 시 리뷰어 모델 상향 검토
4. **`orchestrator_profile_auth_no_tenant` 경고** — 테넌트 미매핑 우회 정책 미결(설계문서 §5)

## 3. 다음 후보 (Phase 3은 대기)

1. 잔여 이슈 1·2 (MLX 반복 루프 / RAG 데이터 감사 — 기존 남은 과제 1번과 동일)
2. P2 관찰 6건 (`.pipeline/REPORT.md`)
3. Supervisor 체크포인터 연결(AsyncPostgresSaver — 상태 직렬화 경계 재설계 필요)
4. (대기) Phase 3: 오케스트레이터(chatbot_id 미지정)를 Supervisor로 통합

## 4. 운영 노트 (이번 세션 추가분)

- **P1 opt-in 켜기**: `docker-compose.override.yml`(로컬 전용, 미추적) api env에 `AIP_SUPERVISOR_ADAPTIVE_REPLAN: "true"` / `AIP_SUPERVISOR_REVIEW_GATE: "true"` 추가 후 `docker compose up -d api`. 부트스트랩 로그 `supervisor_initialized`에서 플래그 확인
- **admin JWT 발급(라이브 테스트용)**: `docker exec aip-api python -c "import jwt,os,time; print(jwt.encode({'sub':'x','role':'ADMIN','security_level_max':'CONFIDENTIAL','user_type':'admin','exp':int(time.time())+7200}, os.environ['AIP_JWT_SECRET'], algorithm='HS256'))"`
- **supervisor 흐름 로그 필터**: `docker logs aip-api | grep -E "supervisor_delegation_done|supervisor_workflow_sticky|supervisor_replan|supervisor_review"`
- 현재 api는 로컬 MLX 모드(`docker-compose.override.yml`, Anthropic 크레딧 소진 대응). 상용 복귀 = override 파일 삭제 후 `docker compose up -d api worker`

## 5. 핵심 문서
- 설계: `docs/architecture/supervisor-agent-design.md` (§7에 Phase 1.5/2 완료 표기·실측 기록)
- 직전 핸드오프: `docs/SESSION-HANDOFF-2026-06-29.md` (남은 과제 1~6 여전히 유효 — RAG 감사, Phase 4 프론트, facts 테이블, non-ADMIN 403)
