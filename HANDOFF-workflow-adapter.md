# HANDOFF — 워크플로우 엔진 ContextAdapter 리팩토링

> 작성: 2026-06-17 / 다음 세션(클린 컨텍스트)에서 착수 / 대상: ai-platform

## 배경 (왜)
챗-퍼스트 사주 디스커버리(묘묘 캐릭터)를 만들며 dynamic 스텝을 **실제 사주 결과값에 grounding**했다. 그 과정에서 **사주 전용 로직을 범용 워크플로우 엔진에 직접 박는 결함**을 만들었다. 엔진은 saju/flowsns/kms 다중 서비스를 서빙해야 하므로, 서비스별 데이터 enrichment는 **어댑터(코드 플러그인)**로 빼고 엔진은 인터페이스만 호출해야 한다. (어댑터 선택 방식: **프로파일에 명시** — 사용자 확정)

## 현재 상태 (커밋됨, 동작 검증 완료)
- ai-platform `main` 최신: `f50374f` (묘묘 + grounding + 연애 상대 비교 시나리오).
- **동작 확인됨**(E2E): 디스커버리 트리거→묘묘 hook(신약 사주 반영)→연애→상대정보 수집→궁합 action→`insight_compat`이 화기/용신 수/궁합결과를 실제값 기반 서술→궁합 리포트 리빌→완료 후 일반대화 복귀.
- saju-backend / 프론트는 무관(이번 변경 없음). 컨테이너는 재빌드돼 라이브.

## 결함 (제거 대상) — `apps/api/src/workflow/engine.py`
범용 엔진에 박힌 사주 전용 코드:
- `_fetch_saju_summary(saju_id)` — saju-backend `/saju/{id}/lookup` 호출 + 오행 한글변환(`_ELEMENT_KO`)
- `_fetch_compat_summary(saju_id)` — `/saju/compatibility/{id}/result` 호출 + 점수 등급화
- `_generate_dynamic` 안에서 위 두 메서드를 직접 호출 + `collected["_saju_summary"]/["_compat_summary"]` 주입
- `import httpx`, `os.environ["AIP_SAJU_BACKEND_URL"]` 의존

## 목표 구조
```
엔진(범용): step 처리·branches·collected·세션·dynamic LLM 호출 + ContextAdapter 인터페이스 호출만
ContextAdapter(인터페이스):  async def enrich(collected: dict) -> dict   # 추가 컨텍스트(예: saju_summary, compat_summary) 반환
  ├ SajuContextAdapter   ← _fetch_saju_summary/_fetch_compat_summary 이전
  ├ FlowsnsContextAdapter (향후)
  └ KmsContextAdapter     (향후)
프로파일.config.context_adapter = "saju"  ← 동적 바인딩(프로파일은 이미 DB CRUD)
```

## 작업 단계 (TODO)
1. **인터페이스 신설**: `apps/api/src/workflow/context_adapter.py`
   - `class WorkflowContextAdapter(Protocol/ABC): async def enrich(self, collected: dict) -> dict`
   - 반환 dict 예: `{"saju_summary": "...", "compat_summary": "..."}` (없으면 빈 dict)
2. **SajuContextAdapter 구현**: 위 `_fetch_saju_summary`/`_fetch_compat_summary`/`_ELEMENT_KO` 로직을 이 어댑터로 이전. saju-backend URL은 생성자 주입(`backend_url`) — `settings.saju_backend_url`(saju_lookup 도구가 쓰는 것)과 동일하게.
3. **엔진 정리**: engine.py에서 사주 fetch 메서드·httpx·os 의존 제거. `__init__`에 `context_adapters: dict[str, WorkflowContextAdapter] | None = None` 추가. `_generate_dynamic`에서:
   - 세션에 바인딩된 어댑터명으로 `adapter = self._context_adapters.get(name)` 조회
   - `extra = await adapter.enrich(collected)` 호출 → `extra`의 값들을 LLM 컨텍스트(`[실제 사주 풀이 근거]`, `[궁합 결과]`)로 주입. (현재 `collected["_saju_summary"]` 직접 참조 → `extra` 기반으로 전환)
4. **바인딩 경로** (프로파일 → 엔진):
   - 프로파일 config에 `context_adapter` 필드 추가(`agent_profile.py` AgentProfile + profile_store 파싱/직렬화 + admin_router 스키마).
   - `ExecutionPlan`에 `context_adapter: str | None` 추가(router에서 profile→plan 전달).
   - `graph_executor._run_workflow_step`(line ~257)에서 `engine.start(workflow_id, session_id, context_adapter=plan.context_adapter)`로 전달. (start 호출부: graph_executor.py:274, workflow.py:45)
   - `engine.start()`가 어댑터명을 세션에 저장(예: `session.collected["_adapter"]` 또는 WorkflowSession 필드) → `advance`/`_generate_dynamic`에서 재사용.
5. **부트스트랩 등록**: `bootstrap.py` WorkflowEngine 생성 시 `context_adapters={"saju": SajuContextAdapter(backend_url=settings.saju_backend_url)}` 주입.
6. **fortune-saju 프로파일**: config에 `context_adapter: "saju"` 설정(admin API 또는 seed yaml).

## 검증
- `python3 -m py_compile` 통과 + aip-api 재빌드(engine.py 소스).
- E2E: 디스커버리 연애 시나리오 재현 — 묘묘 통찰이 여전히 실제 사주/궁합 기반인지(현재와 동일 결과). 어댑터 미바인딩 프로파일은 grounding 없이도 동작(폴백).

## 곁다리 폴리시 (같이 처리 가능)
- 궁합 `action` 스텝 후 기본 문구 **"처리가 완료되었습니다." 노출** 제거: `run_compat_male/female`의 `on_success_message`를 빈 출력으로(엔진에서 빈 메시지 허용하도록 `_execute_action_step` 기본값 처리 보완) 또는 묘묘 톤 한 줄로.
- 캐릭터: 현재 묘묘. 천명으로 되돌리려면 `saju-discovery.yaml`의 `_persona` 앵커만 교체.
- 캐릭터 아바타 이미지: 텍스트→이미지 생성 툴 없음 → 소유자가 에셋 제공 시 프로필/프론트에 연결. (현재 미연결)

## 참고 파일
- 엔진: `apps/api/src/workflow/engine.py` (_generate_dynamic, _fetch_saju_summary, _fetch_compat_summary)
- 정의/스토어: `workflow/definition.py`(WorkflowStep.system), `workflow/store.py`(파싱/직렬화)
- 워크플로우: `apps/api/seeds/workflows/saju-discovery.yaml`
- 호출부: `agent/graph_executor.py:250 _run_workflow_step`, `gateway/routes/workflow.py:45`
- 프로파일: `seeds/profiles/fortune-saju.yaml`, `agent/profile_store.py`, `domain/agent_profile.py`, `gateway/admin_router.py`
- 워크플로우 CRUD(이미 존재): `admin_router.py` `POST/PUT/DELETE /workflows`, `WorkflowStore.create/update/delete`
