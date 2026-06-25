# ADR-012 — Profile 행동 필드 배선 + 모델 override seam + router_model KNOWN GAP

- **상태**: 채택 (2026-06-25)
- **결정자**: 파이프라인 (Planner/Reviewer 합의)
- **커밋**: 798ae07 (main merge)
- **관련 ADR**: [ADR-009 RAG 단일 정보원](adr-009-rag-single-source-of-truth.md), [ADR-011 KMS 도메인 매핑](adr-011-kms-domain-mapping.md)

---

## 맥락

플랫폼 핵심 철학은 **"Agent는 하나, Profile YAML만 바꾸면 행동이 바뀐다"**이다. 그러나 전수조사 결과 `AgentProfile`의 행동 필드 33개 중 **16개(48%)만 완전히 흐르고**, 5개는 부분 동작, 12개는 선언만 된 채 행동에 반영되지 않는 "죽은 필드"였다.

핵심 증상:
- `planning_disabled`는 로더가 YAML 키를 파싱하지 않아 YAML로 켤 수 없었다. 소비처(`strategy_builder:186`)가 `getattr(profile, "planning_disabled", False)`로 방어한 것 자체가 로더 갭의 흔적이었다.
- `main_model`/`router_model`은 파싱·직렬화는 되나 팩토리가 항상 `settings.main_model`(환경 변수)만 사용했다. 별건 GEPA PoC에서 saju 프로필의 `main_model: sonnet`이 무시되고 `provider_mode` 기반 Haiku로 resolve된 혼란이 이 갭의 직접 증상으로 spot-check에서 재확인됐다.
- `max_tool_calls`는 ExecutionPlan까지 왔지만 agentic 루프에 실제 카운트 기반 상한 코드가 없었다.
- `agent_timeout_seconds`는 에이전트 경로에서 전역 `settings.planner_timeout`만 사용했다.

추가로, 소비처가 없는 필드 5개(`validation_nudge_*`, `execution_path`, `llm_system_prefix` Profile 필드)가 dataclass에 존재해 "죽어 있지만 손댈 수 없는" 표면을 키우고 있었다.

---

## 결정

### 1. 로더 라운드트립 게이트 (재발 영구 차단)

`AgentProfile` dataclass의 **모든 필드가 `_parse_profile`에 의해 파싱되고 `_profile_to_dict`에 의해 직렬화됨**을 단위 테스트(`test_profile_roundtrip.py`)로 영구 강제한다.

- 실제 로더(`ProfileStore.parse_profile`, `ProfileStore.profile_to_dict`)를 경유 — MagicMock 직접 생성 금지.
- 의도적 미라운드트립 필드는 명시적 allowlist + 정당화만 허용.
- 이를 통해 `planning_disabled`의 RED→GREEN을 증명하고, 향후 필드 추가 시 로더 동기화 누락을 자동으로 탐지한다.

### 2. 별칭 해석기 — Settings-only 순수 함수 (infra 레이어)

새 파일 `infrastructure/providers/model_aliases.py`에 `resolve_model_alias(alias: str, settings: Settings) -> str` 순수 함수를 둔다.

설계 원칙:
- **Settings만 읽음** — plan, Router, factory 인스턴스 import 없음. 레이어 단방향(C7 infra).
- 미지정·미인식 별칭 → `""` 반환(호출부가 부트스트랩 기본값으로 안전 폴백, 회귀 0).
- `provider_mode` 백엔드(anthropic/ollama/http/openai)별 매핑 테이블 내장. 별칭이 아닌 구체 모델 ID는 pass-through.

반려한 대안: resolver를 `StrategyBuilder`(C2)에 두는 방안 — C2가 infra Settings 백엔드 결정을 알아야 하므로 레이어 역방향. C7에 두고 C3(executor)가 호출하는 구조가 단방향에 부합.

### 3. main_model override seam (요청별 override, 그래프 캐시 우회)

- `ExecutionPlan`에 `main_model: str = ""`, `router_model: str = ""` 필드 추가. 빈값 = 미지정 = env 폴백.
- `StrategyBuilder.build()`가 raw alias를 plan에 전달(C2는 해석 안 함).
- `GraphExecutor.__init__`에 `provider_factory`와 `settings`를 **선택적**으로 주입 — 없으면 레거시 경로(회귀 0).
- `_effective_agentic_app`에서 `plan.main_model`을 `resolve_model_alias`로 해석 → 비어 있지 않고 부트스트랩 기본과 다르면 `provider_factory.get_chat_model(model_name=resolved)`로 요청별 override `chat_model` 생성, 새 `agent_app` 컴파일. 아니면 기존 `self._chat_model` 사용.
- 그래프 캐시 키는 모델-무관 — override 시 캐시 우회가 정당.

이 seam의 핵심 불변: **"팩토리 = 설정만 받아 생성" 패턴 유지**. 모델 정보는 Router→plan→Agent 단방향. 미지정/미해석 기본 경로는 코드·모델 불변.

코드 리뷰 nit 사항: `agent_profile.py`에서 `router_model`/`main_model`의 기본값이 `""` 대신 `"haiku"`/`"sonnet"`으로 설정되어 있다. 명시적 `""` 설정 시에만 폴백이 발동되는 의도이며, 다음 이터레이션에서 기본값 정책을 ADR로 명시할 것을 권고한다.

### 4. P0-4 max_tool_calls — 카운트 기반 정밀 상한

`recursion_limit`은 노드당 1 step으로 근사되어 툴 호출 정확 상한이 아니다. 실행된 tool 메시지 수를 직접 세어 `plan.max_tool_calls`에서 상한을 적용한다.

- `recursion_limit = 2 * max_tool_calls + 1`을 1차 런어웨이 가드로 설정(폭주 방지).
- 정확 상한 = tool 메시지 카운트(ainvoke: 결과 메시지 검사, astream: `on_tool_start` 이벤트 카운트).
- 초과 시: 에러 미전파, 부분 답변 반환 + WARN 로그.

### 5. P0-5 agent_timeout_seconds — asyncio 타임아웃

- ainvoke 경로: `asyncio.wait_for(agent_app.ainvoke(...), timeout=plan.agent_timeout_seconds)`.
- astream 경로: `asyncio.timeout(plan.agent_timeout_seconds)` context manager.
- 타임아웃 시: 친화 메시지 반환 + WARN 로그(삼키지 않음). 스트리밍 경로는 이미 전송된 부분 토큰 보존 + clean done 이벤트.
- 미지정 시 기존 기본값(30s) 그대로.

### 6. P1 죽은 필드 정직성 정리

제거 기준: **소비처 0 + 미구현 스텁**. 외부(admin UI/BFF) 참조 여부를 grep으로 1차 확인 후 제거.

- 제거: `validation_nudge_enabled`, `validation_nudge_interval`, `validation_nudge_type`, `execution_path`, `llm_system_prefix` (Profile dataclass 필드).
- 동명이의 주의: `llm_system_prefix`는 (1) Profile 필드=제거 대상, (2) `Settings.llm_system_prefix` + locale/factory의 LIVE 메커니즘=절대 불변. 이 둘은 완전히 별개다.
- 유지 + NOT WIRED 주석: `category_scopes` — `strategy_builder:81`이 `SearchScope.category_ids`에 세팅하지만 vector_store가 category 컬럼을 읽지 않음. admin_router.py가 참조해 제거 불가.
- 크로스앱: `apps/frontend/.../yaml-schema.ts`에서 `execution_path`, `validation_nudge_enabled` 2엔트리 삭제.

---

## KNOWN GAP: router_model 라우팅 LLM 실호출 swap (중요, 다음 이터레이션)

> **이 갭은 의도적이고 정직하게 기록된다. 숨기지 않는다.**

`router_model`은 `ExecutionPlan`까지 흐르고 별칭 해석도 동작하지만, **AIRouter 내부 라우팅 LLM의 실제 교체는 이번 이터레이션에서 구현되지 않았다.**

구조적 이유:
- `AIRouter.__init__`에서 `ChainResolver`/`IntentClassifier`/`SemanticClassifier`가 `router_llm`을 생성자에서 1회 바인딩한다(`ai_router.py:45-50`).
- 이 서브컴포넌트들은 plan 생성 이전의 L0/L1/L2에서 호출된다.
- `factory.get_router_llm`은 `get_chat_model`과 달리 `model_name` 파라미터가 없다. `LLMProvider` 인터페이스에 per-call override가 없다.
- 라우팅 LLM swap = L0/L1/L2 핫패스(최고트래픽·민감 fallback) 구조 변경 → 회귀 위험 크다.

**다음 이터레이션 seam**: `AIRouter.route(profile)`에서 `profile.router_model`을 `resolve_model_alias`로 해석 → 서브컴포넌트들에 per-request override LLM 주입. 이를 위해 `LLMProvider` 인터페이스 또는 서브컴포넌트 생성자에 선택적 override 파라미터 추가가 필요하다.

현재 상태 요약:
- `router_model: sonnet`을 YAML에 써도 라우팅 LLM은 부트스트랩 기본값을 그대로 사용한다.
- plan에는 값이 흐르고, 테스트가 이를 assert한다.

---

## 결과

### 긍정적

- 플랫폼 철학이 실질적으로 복원됐다. YAML 편집 → 행동 변경의 신뢰성이 높아졌다.
- 로더 라운드트립 테스트가 미래의 "선언됐지만 죽은 필드" 재발을 자동으로 탐지한다.
- 회귀 위험 최대인 모델 배선을 "optional injection + empty fallback" 패턴으로 해결해 기존 경로 완전 불변을 보장했다.
- 죽은 필드 제거로 Profile 표면이 솔직해졌다.

### 부정적 / 트레이드오프

- `main_model` override 시 매 요청마다 새 `agent_app`을 컴파일해야 한다(그래프 캐시 우회). 모델 고정 프로필의 다수 환경에서는 영향 없음. 빈번한 모델 변경 환경에서는 컴파일 비용 증가 가능.
- P0-3 router_model 실호출 swap 미완으로 `router_model` YAML 설정이 현재 동작에 반영되지 않는다는 사실을 운영자가 인지해야 한다.
- `agent_profile.py`의 `main_model`/`router_model` 기본값이 `"haiku"`/`"sonnet"`이어서 미지정의 의미가 명확하지 않다(차후 ADR로 정책화 필요).

### 테스트

신규 3파일, 44 tests 추가. 전체 1272 passed / 9 skipped / 0 fail.

- `test_profile_roundtrip.py`: 로더 라운드트립(RED→GREEN), 직렬화 완전성 가드.
- `test_model_wiring.py`: `resolve_model_alias` 다중 백엔드, plan 배선, override seam(스텁 팩토리), 회귀(미지정→기본값 불변).
- `test_agent_caps.py`: `max_tool_calls` 카운트 캡(ainvoke/astream), `agent_timeout_seconds` 친화 종료(ainvoke/astream).

---

## 변경 파일

| 파일 | 변경 |
|------|------|
| `apps/api/src/agent/profile_store.py` | `_parse_profile` planning_disabled 파싱 + `_profile_to_dict` 직렬화 2줄 추가 |
| `apps/api/src/domain/agent_profile.py` | 죽은 필드 5개 제거 + category_scopes NOT WIRED 주석 |
| `apps/api/src/router/execution_plan.py` | `main_model`, `router_model` 필드 추가 |
| `apps/api/src/router/strategy_builder.py` | `build()` 반환 시 plan에 필드 전달 + category_scopes NOT WIRED 주석 |
| `apps/api/src/infrastructure/providers/model_aliases.py` | 신규 — `resolve_model_alias` 순수 함수 |
| `apps/api/src/agent/graph_executor.py` | 선택적 factory/settings 주입 + `_effective_agentic_app` override seam + max_tool_calls 캡 + agent_timeout_seconds wait_for |
| `apps/api/src/bootstrap.py` | `GraphExecutor`에 factory/settings 주입 |
| `apps/frontend/.../yaml-schema.ts` | `execution_path`, `validation_nudge_enabled` 2엔트리 삭제 |
| `apps/api/tests/test_profile_roundtrip.py` | 신규 |
| `apps/api/tests/test_model_wiring.py` | 신규 |
| `apps/api/tests/test_agent_caps.py` | 신규 |
