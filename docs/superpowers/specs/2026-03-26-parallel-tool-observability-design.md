# 병렬 Tool 실행 + 노드별 레이턴시 설계

**날짜**: 2026-03-26
**목표**: 독립 Tool 호출을 병렬 실행하여 응답시간 단축 + Tool 레이턴시를 RequestTrace에 통합
**접근법**: Anthropic Parallelization 패턴(Sectioning) — ExecutionPlan에 tool_groups 도입

---

## 배경

### 현재 문제

1. **순차 Tool 실행**: `nodes.py:48`에서 순차 for loop — 독립 도구도 순차 실행
2. **Observability 갭**: Tool 레이턴시가 로그에만 기록되고 RequestTrace에 미통합

### Anthropic "Building Effective Agents" 권고

**Parallelization — Sectioning 패턴**:
- 사전 정의된 독립 서브태스크를 병렬 실행하고 결과를 프로그래밍 방식으로 합산
- Orchestrator-Workers(동적 분배)와 구분: 서브태스크가 사전 정의됨

### 기대 효과

- 비교 질문: rag_search 2회 병렬 시 응답시간 약 50% 감소
- 일반 질문: rag_search + fact_lookup 병렬 시 레이턴시 감소
- 향후 Tool 추가(인터넷검색, RDB, MCP) 시 자동 병렬화

---

## 아키텍처

### 데이터 모델

ToolCall frozen dataclass (tool_name + params).
ExecutionPlan.tools를 tool_groups: list[list[ToolCall]]로 대체.
그룹 내 병렬, 그룹 간 순차.

### 그룹 배정 규칙

- GREETING, SYSTEM_META: 빈 리스트 (도구 없음)
- 일반 (STANDALONE 등): 모든 도구를 한 그룹에 배정 (= 전부 병렬)
- 기본: 한 그룹 = 전부 병렬

### 실행 엔진

asyncio.gather(*tasks, return_exceptions=True) — 한 도구 실패해도 나머지 결과 사용.

### Observability 통합

trace.start_node/finish로 Tool 레이턴시를 RequestTrace에 통합.

---

## 변경 파일

- src/router/execution_plan.py: ToolCall 추가, tools -> tool_groups
- src/router/strategy_builder.py: 그룹 배정 로직
- src/agent/nodes.py: asyncio.gather 병렬 실행
- src/agent/graph_executor.py: plan.tool_groups 참조

---

## 보류 항목

1. CROSS_DOC 쿼리 자동 분해 — Router에서 비교 대상 추출 로직 필요
2. MetricsCollector 활성화 — RequestTrace만으로 충분
3. LLM/VectorStore 내부 레이턴시 — Tool 레벨 측정으로 충분
