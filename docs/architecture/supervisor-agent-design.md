# Supervisor Agent 아키텍처 설계 (초안, 2026-07-08)

> 상태: 설계 초안 (구현 전). RAG 검증 우선이라 설계만 확정해 둔다.
> 목표: 현재 "플랫 라우터 + 핸드오프"에서 "Supervisor(메인) ↔ Sub-AI(프로파일)" 계층 구조로 전환.

## 0. 핵심 원칙 (불변 제약)

1. **Supervisor는 additive 레이어다.** 서브 AI(프로파일)는 **단독 호출 가능성을 그대로 유지**한다.
   - 외부 서비스는 대부분 챗봇 하나만 필요 → `chatbot_id` 지정 시 그 프로파일이 **단독으로 완결**되어야 한다.
   - Supervisor는 "여러 프로파일을 오케스트레이션해야 할 때만" 얹는 옵션이다.
2. **Deny-by-default 유지.** 이미 `AIP_PROFILE_AUTH_STRICT=true`로 켜져 있다. Supervisor 경로든 직접 경로든
   호출자의 `allowed_profiles`(API Key/JWT/테넌트) 밖의 프로파일은 절대 못 탄다.
3. **컨텍스트 소유권은 진입 모드가 결정한다.** 직접 모드=해당 프로파일이 소유, Supervisor 모드=메인이 소유.
4. **Hub-and-spoke — 프로파일 간 peer 라우팅 금지.** 라우팅·위임 결정권은 **메인 한 곳**. 서브는 메인하고만 통신
   (안 맞으면 다른 서브로 넘기지 말고 메인에 반환), 다음 행동은 메인이 결정. mesh(A→B→A 핑퐁) 금지, 메인이 위임
   깊이/횟수를 캡으로 통제. 의미분석은 메인이 앞단에서 1회. 위임은 사용자엔 invisible, 운영자엔 트레이스로 transparent.

## 1. 배경 / 동기

**업계 흐름**: 단일 거대 에이전트 → **Supervisor(오케스트레이터) + 전문 Sub-Agent** 계층 구조로 수렴
(LangGraph Supervisor, OpenAI Swarm/Agents SDK, Anthropic multi-agent 등). 이유:

- **보안 단일 관문**: 서브 호출을 메인이 전부 게이트 → 인가/스코프를 한 곳에서 강제.
- **멀티도메인 질의**: 메인이 분해 → 여러 서브에 위임 → 종합. (핸드오프는 한 턴에 한 도메인만)
- **관측/제어**: 위임 트리가 곧 트레이스. 루프/실패를 메인이 관장.

**현재 한계** (`orchestrator.py` MasterOrchestrator): 3-Tier 라우팅으로 **프로파일 하나를 골라 제어를 넘긴다**
(`selected_profile_id` + `_handle_switch` 핸드오프). 메인이 위에서 계속 관장하지 않아 멀티도메인·집계가 약하다.

## 2. 두 모드 (공존)

```
[직접 모드]  chatbot_id = "insurance-qa"     → 해당 프로파일 단독 실행 (지금과 동일, 변경 없음)
                                                외부 서비스 대부분이 이 경로.

[Supervisor] chatbot_id = "supervisor"(신규)  → 메인이 질의 분석 → 인가된 서브에 위임
             또는 chatbot_id 생략(오케스트레이터)   → 서브 결과를 컨텍스트로 수집 → 종합/후속위임 루프
```

- **직접 모드는 손대지 않는다.** `general-chat`(통합 AI)를 포함한 모든 프로파일은 `chatbot_id`로 단독 호출 가능.
- **Supervisor는 별도 프로파일/모드**로 추가한다. `general-chat`을 Supervisor로 승격할지, 별도 `supervisor` 프로파일을
  만들지는 §6 결정사항.

## 3. 목표 아키텍처

```
                 ┌──────────────────────────────────────────┐
   요청 ──인증──▶ │  진입 라우팅 (chatbot_id 유무)              │
   (allowed_       └───────────────┬──────────────┬───────────┘
    profiles)                      │직접           │supervisor/미지정
                                   ▼               ▼
                       ┌─────────────────┐   ┌───────────────────────────┐
                       │ Sub-AI 단독 실행 │   │  Supervisor (메인)          │
                       │ (기존 그래프)    │   │  - 질의 분해                │
                       └─────────────────┘   │  - 인가된 서브 선택(deny-def)│
                                             │  - 서브 위임(도구처럼 호출) │
                                             │  - 컨텍스트 수집·종합       │
                                             │  - 후속 위임 루프/종료 판정 │
                                             └───────────┬───────────────┘
                                                         │ delegate (scoped)
                                          ┌──────────────┼──────────────┐
                                          ▼              ▼              ▼
                                   insurance-qa    kms-assistant   fortune-saju ...
                                   (Sub-AI = 기존 프로파일 그래프, 그대로 재사용)
```

**Sub-AI = 기존 프로파일 실행 그래프를 그대로 재사용.** Supervisor는 서브를 "호출 가능한 능력(tool/subagent)"으로
감싸기만 한다. 그래서 서브의 단독성이 자동 보존된다.

## 4. Supervisor 실행 루프 (의사코드)

```
supervise(question, ctx):
    allowed = resolve_allowed_profiles(ctx)          # deny-by-default, 이미 구현됨
    plan    = main_llm.decompose(question, allowed)  # 어떤 서브에 무엇을 위임할지
    results = []
    for step in plan.delegations:                    # 순차 or 병렬
        if step.profile not in allowed: continue     # 단일 관문에서 스코프 강제
        sub_ctx = derive_scoped_context(ctx, step)   # 서브에는 필요한 범위만 위임
        r = run_subagent(step.profile, step.subquery, sub_ctx)  # 기존 그래프. 서브는 메인에만 반환(§0-4)
        v = main_llm.review(r, evidence=r.sources)   # 검토 게이트(P1): 판정(pass/fail·주석), 재생성 아님
        results.append({"answer": r, "verdict": v})  # 근거=서브 청크(트레이스 재사용). 서브 가드레일과 중복 금지
        if plan.is_adaptive: plan = main_llm.replan(question, results, allowed)  # 후속 위임(메인 결정)
    return main_llm.synthesize(question, results)    # 메인이 종합·응답 소유 (검토 통과분 기준)
```

- **위임 스코프 파생**(`derive_scoped_context`): 서브에는 필요한 도메인/문서 범위만 넘겨 최소권한 유지.
- **루프 상한**: 위임 횟수/깊이 캡(무한 위임 방지). RAG의 재시도 캡과 동일 철학.
- **트레이스**: 위임 트리를 그대로 트레이스 노드로 — 우리가 만든 트레이스 패널이 그대로 계층 표시.

## 5. 보안 (deny-by-default, 이미 On)

- 진입 인증에서 `allowed_profiles` 결정 (API Key `{*}`/`{fortune-saju}`, JWT ADMIN→`["*"]` / 비ADMIN→`[]`).
- **Supervisor는 위임 직전 매 서브마다 `is_profile_allowed` 재검사** → 단일 관문에서 스코프 초과 위임 차단.
- 직접 모드도 동일 인가를 통과(기존과 동일).
- **개선 여지**: `orchestrator_profile_auth_no_tenant` — 테넌트 매핑 없을 때 테넌트 필터 우회. 멀티테넌트
  격리를 강제하려면 "테넌트 없음 = deny(또는 명시적 default tenant)"로 정책화 검토.

## 6. 결정 사항 (구현 전 확정 필요)

1. **Supervisor의 정체**: (a) `general-chat`을 Supervisor로 승격 vs (b) 신규 `supervisor` 프로파일 분리.
   → 권장 (b): 통합 AI는 "전 스코프 단독 답변" 용도로 남기고, Supervisor는 별도로. 역할 혼선 방지.
2. **위임 실행 방식**: 서브를 (a) in-process 함수 호출 vs (b) 내부 HTTP(`/chat`) 재귀. → 권장 (a) in-process(지연↓).
3. **위임 병렬성**: 순차(단순) vs 병렬(멀티도메인 지연↓). → 초기 순차, 이후 병렬 확장.
4. **컨텍스트 위임 범위**: 전체 대화 vs 서브쿼리+필요범위만. → 최소권한 원칙으로 후자.
5. **메모리 소유**: Supervisor 세션 메모리 vs 서브별 메모리. → 메인 소유 + 서브는 stateless 호출.

## 7. 마이그레이션 (증분, 저위험)

직접 경로를 안 건드리므로 단계적:

1. **Phase 0**: 서브 실행을 함수로 캡슐화(`run_subagent(profile_id, query, ctx)`) — 기존 그래프 래핑.
2. **Phase 1**: `supervisor` 프로파일 + 최소 루프(decompose→순차위임→synthesize) 추가. 직접 모드 무변경.
3. **Phase 2**: adaptive replan(후속 위임 루프) + 병렬 위임 + 위임 트레이스 노드.
4. **Phase 3**: 오케스트레이터(chatbot_id 미지정)를 Supervisor로 통합 — 라우팅=1개 위임의 특수케이스로 흡수.

각 Phase는 직접 모드 회귀 없음을 e2e로 검증(외부 서비스 단일 챗봇 시나리오 필수 통과).

## 8. 열린 질문

- 위임 실패/부분성공 시 메인의 degrade 전략(부분 답변 vs 재위임 vs 사과)?
- 서브가 또 Supervisor여야 하는 2-depth 위임을 허용할지(현재는 1-depth 권장).
- 비용/지연 예산: 위임 N개 = LLM 호출 N+2회(decompose+synthesize). 로컬 모델 지연과 상충 → 병렬+캡 필수.

---

*근거 코드: `orchestrator/orchestrator.py`(현 라우팅), `domain/profile_authz.py`(deny-default), `agent/graphs.py`(프로파일 그래프),
`agent/graph_executor.py`(실행). 현 strict=on(`AIP_PROFILE_AUTH_STRICT=true`) 전제.*
