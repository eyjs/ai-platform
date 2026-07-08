# Supervisor e2e 인수 시나리오 (박아둠, 2026-07-08)

> 상태: Supervisor P0 구현의 **필수 e2e 인수 테스트**. Integrator 통합 게이트에 포함.
> 원칙: 아래 3개가 전부 green이어야 P0 완료로 인정한다. (B는 회귀 0 절대 게이트)
> 구현 매핑: A/C → 신규 e2e, B → task-007. 인터페이스 확정 후 실제 pytest로 구현.

## 공통 픽스처
- `AIP_PROFILE_AUTH_STRICT=true` (deny-by-default On) 전제.
- 벡터스토어 시드(테스트 전용, teardown 격리):
  - 도메인 `insurance`(또는 `_common`)에 "자기부담금/보험가격지수" 포함 문서.
  - 도메인 `kms`에 "보험금 청구 절차" 포함 문서.
  - (멀티도메인 A가 두 도메인 모두를 실제로 검색·회수할 수 있어야 함 — 시드 없으면 A는 회수 0이라 무의미. 시드 필수.)
- 자격증명:
  - `admin`(allowed_profiles=`{*}`) — A, B용.
  - 스코프 키 `{fortune-saju}` — C용.

---

## 시나리오 A — 멀티도메인 위임·종합 (Supervisor 핵심 가치)

**Given** admin 자격 + 위 시드, `chatbot_id="supervisor"`
**When** `POST /chat/stream {q:"실손보험 자기부담금이 얼마인지랑, 사내 규정의 보험금 청구 절차도 알려줘"}`
**Then (assert)**
1. `decompose`가 **위임 2건**을 만든다: `insurance-qa`(자기부담금) + `kms-assistant`(청구 절차). (1건만 나오면 실패)
2. 각 위임 직전 `is_profile_allowed(profile, {*})` 재검사 통과 로그/트레이스 존재(§0-3).
3. 두 서브 모두 `run_subagent`로 실행되고 **각각 메인에 반환**된다(서브→서브 호출 트레이스 부재, §0-5).
4. 최종 답변에 **자기부담금 정보 AND 청구 절차 정보가 둘 다** 포함(종합됨). (한쪽만 있으면 실패 — 이게 현행 플랫 라우터 대비 이득)
5. 트레이스에 위임 트리(2개 서브)가 관측된다.

**Pass**: 위 5개 전부. 특히 4번(양 도메인 종합)이 핵심.

---

## 시나리오 B — 직접 모드 회귀 0 (절대 게이트, task-007)

**Given** 외부 서비스가 챗봇 하나만 사용, `chatbot_id="insurance-qa"`
**When** `POST /chat/stream {q:"보험가격지수가 뭐야"}`
**Then (assert)**
1. **Supervisor 레이어를 타지 않는다**: `SubAgentRunner`/`supervisor.*` 코드 경로 미진입(스파이/로그로 증명).
2. 응답이 **supervisor 도입 전과 동일**: 동일 프로파일 그래프(RAG→답변) 단독 실행. 답변에 보험가격지수 정의 포함.
3. `insurance-qa` 외 다른 프로파일로 위임/핸드오프 없음.

**Pass**: 3개 전부. **이 시나리오가 하나라도 깨지면 통합 중단**(§0-2 직접모드 무변경 위반).

---

## 시나리오 C — deny-by-default (스코프 밖 위임 차단)

**Given** `{fortune-saju}` 스코프 자격, `chatbot_id="supervisor"`
**When** `POST /chat/stream {q:"보험 자기부담금 알려줘"}`  (보험은 이 자격 밖)
**Then (assert)**
1. `decompose`가 `insurance-qa` 위임을 만들어도, 위임 직전 `is_profile_allowed(insurance-qa, {fortune-saju})` → **deny → 위임 스킵**(§0-3, P0-4).
2. `run_subagent(insurance-qa, ...)`가 **실제로 호출되지 않는다**(스파이로 0회 증명).
3. 인가된 서브가 없어 메인이 "권한 범위 밖" 계열 응답으로 안전 종료(500/크래시 아님).

**Pass**: 3개 전부. 특히 2번(스코프 밖 서브 미호출)이 보안 핵심.

---

## 부가 게이트 (P0 DoD 연계)
- **위임 상한(C가 아닌 별도 케이스)**: 캡 초과 위임 시도 시 무한루프 없이 안전 종료 + "불완전 표시"(§P0-6, degrade 최소안).
- **hub 정적검증(task-008)**: 서브 실행 경로에서 다른 프로파일로 위임/라우팅하는 심볼 참조가 코드상 부재(grep/AST).

## 비고
- A는 **시드 의존** — 시드 없으면 회수 0이라 "종합" 검증 불가. 픽스처에서 두 도메인 문서를 반드시 인덱싱하고 teardown.
- P1(검토 게이트·병렬·adaptive)은 이 스펙 범위 아님. P1 착수 시 A에 "검토 통과분만 종합" 어서션 추가 예정.
