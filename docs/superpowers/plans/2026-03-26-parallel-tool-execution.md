# 병렬 Tool 실행 + 노드별 레이턴시 구현 계획

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 독립 Tool 호출을 asyncio.gather로 병렬 실행하고, Tool 레이턴시를 RequestTrace에 통합한다.

**Architecture:** ExecutionPlan.tools를 tool_groups: list[list[ToolCall]]로 대체. 그룹 내 병렬(asyncio.gather), 그룹 간 순차. RequestTrace.start_node/finish로 각 Tool 레이턴시 자동 기록.

**Tech Stack:** Python 3.12, asyncio, dataclass, pytest

---

## Context

현재 `nodes.py:48`에서 `for tool in plan.tools: await registry.execute(...)` — 모든 도구가 순차 실행됨.
`rag_search` + `fact_lookup`이 독립적인데도 직렬로 실행되어 불필요한 대기 발생.

Anthropic "Building Effective Agents" Parallelization(Sectioning) 패턴 적용:
사전 정의된 독립 서브태스크를 병렬 실행하고 결과를 합산.

**변경 대상 파일과 `plan.tools` 참조 위치:**
- `src/router/execution_plan.py:41` — `tools: list` 필드 정의
- `src/agent/nodes.py:48` — `for tool in plan.tools:` 순차 루프
- `src/agent/graph_executor.py:394,461` — AGENTIC 모드 `convert_tools_to_langchain(plan.tools, ...)`
- `src/router/ai_router.py:106` — `tools_count=len(plan.tools)` 로깅
- `src/router/strategy_builder.py:91` — `tools=tools` ExecutionPlan 생성
- `tests/test_graphs.py:101,169,233` — `tools=[FakeTool()]` 테스트

---

## 파일 구조

| 작업 | 파일 | 역할 |
|------|------|------|
| 수정 | `src/router/execution_plan.py` | ToolCall dataclass 추가, tools → tool_groups |
| 수정 | `src/router/strategy_builder.py` | tool_groups 배정 로직 |
| 수정 | `src/agent/nodes.py` | asyncio.gather 병렬 실행 + trace 통합 |
| 수정 | `src/agent/graph_executor.py` | plan.tools → plan.tool_groups (AGENTIC 모드) |
| 수정 | `src/router/ai_router.py:106` | plan.tools → plan.tool_groups 로깅 |
| 수정 | `tests/test_graphs.py` | tools=[FakeTool()] → tool_groups=[[ToolCall(...)]] |
| 생성 | `tests/test_parallel_tools.py` | 병렬 실행 + 에러 처리 + trace 통합 테스트 |

---

## Chunk 1: 데이터 모델 + 병렬 실행

### Task 1: ToolCall dataclass + ExecutionPlan 변경

**Files:**
- Modify: `src/router/execution_plan.py`
- Test: `tests/test_parallel_tools.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_parallel_tools.py
"""병렬 Tool 실행 테스트."""
import pytest
from src.router.execution_plan import ToolCall, ExecutionPlan
from src.domain.models import AgentMode, SearchScope


def test_tool_call_frozen():
    tc = ToolCall(tool_name="rag_search", params={"query": "test"})
    assert tc.tool_name == "rag_search"
    assert tc.params == {"query": "test"}
    with pytest.raises(AttributeError):
        tc.tool_name = "other"


def test_tool_call_default_params():
    tc = ToolCall(tool_name="fact_lookup")
    assert tc.params == {}


def test_execution_plan_tool_groups():
    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[
            ToolCall("rag_search", {"query": "test"}),
            ToolCall("fact_lookup", {"query": "test"}),
        ]],
    )
    assert len(plan.tool_groups) == 1
    assert len(plan.tool_groups[0]) == 2


def test_execution_plan_empty_tool_groups():
    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
    )
    assert plan.tool_groups == []
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_parallel_tools.py -x -v`
Expected: ImportError (ToolCall 미존재)

- [ ] **Step 3: execution_plan.py 수정**

`src/router/execution_plan.py`에 ToolCall 추가, tools → tool_groups 변경:

```python
@dataclass(frozen=True)
class ToolCall:
    """개별 도구 호출 단위. tool_name + params."""
    tool_name: str
    params: dict = field(default_factory=dict)
```

ExecutionPlan 변경:
```python
# 변경 전
tools: list = field(default_factory=list)

# 변경 후
tool_groups: list[list[ToolCall]] = field(default_factory=list)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_parallel_tools.py -x -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/router/execution_plan.py tests/test_parallel_tools.py
git commit -m "feat: ToolCall dataclass + ExecutionPlan.tool_groups 도입"
```

---

### Task 2: nodes.py 병렬 실행 + trace 통합

**Files:**
- Modify: `src/agent/nodes.py:37-86`
- Test: `tests/test_parallel_tools.py` (추가)

- [ ] **Step 1: 병렬 실행 테스트 추가**

```python
# tests/test_parallel_tools.py에 추가
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock
from src.tools.base import ToolResult
from src.router.execution_plan import ToolCall, ExecutionPlan, QuestionStrategy, QuestionType
from src.domain.models import AgentMode, SearchScope
from src.agent.state import create_initial_state
from src.observability.trace_logger import RequestTrace


@pytest.mark.asyncio
async def test_parallel_execution_faster_than_sequential():
    """2개 도구 병렬 실행이 순차보다 빠른지 검증."""
    from src.agent.nodes import create_execute_tools

    async def slow_execute(tool_name, params, context, scope=None):
        await asyncio.sleep(0.1)
        return ToolResult(success=True, data=[{"chunk_id": f"{tool_name}-1", "content": "c", "score": 0.9}])

    registry = AsyncMock()
    registry.execute = AsyncMock(side_effect=slow_execute)

    execute_tools = create_execute_tools(registry)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[
            ToolCall("rag_search", {"query": "test"}),
            ToolCall("fact_lookup", {"query": "test"}),
        ]],
        question_type=QuestionType.STANDALONE,
    )
    state = create_initial_state("test", plan, "s1")

    t_start = time.time()
    result = await execute_tools(state)
    elapsed = time.time() - t_start

    assert len(result["search_results"]) == 2
    assert set(result["tools_called"]) == {"rag_search", "fact_lookup"}
    # 병렬이면 ~0.1s, 순차면 ~0.2s
    assert elapsed < 0.18, f"Expected parallel execution, took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_parallel_execution_one_failure():
    """한 도구 실패해도 나머지 결과 반환."""
    from src.agent.nodes import create_execute_tools

    async def mixed_execute(tool_name, params, context, scope=None):
        if tool_name == "rag_search":
            return ToolResult(success=True, data=[{"chunk_id": "c1", "content": "c", "score": 0.9}])
        raise RuntimeError("fact_lookup failed")

    registry = AsyncMock()
    registry.execute = AsyncMock(side_effect=mixed_execute)

    execute_tools = create_execute_tools(registry)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[
            ToolCall("rag_search", {"query": "test"}),
            ToolCall("fact_lookup", {"query": "test"}),
        ]],
    )
    state = create_initial_state("test", plan, "s1")

    result = await execute_tools(state)
    assert len(result["search_results"]) == 1
    assert result["tools_called"] == ["rag_search"]


@pytest.mark.asyncio
async def test_sequential_groups():
    """2개 그룹이 순차 실행되는지 검증."""
    from src.agent.nodes import create_execute_tools

    call_order = []

    async def ordered_execute(tool_name, params, context, scope=None):
        call_order.append(tool_name)
        return ToolResult(success=True, data=[])

    registry = AsyncMock()
    registry.execute = AsyncMock(side_effect=ordered_execute)

    execute_tools = create_execute_tools(registry)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[
            [ToolCall("tool_a", {})],
            [ToolCall("tool_b", {})],
        ],
    )
    state = create_initial_state("test", plan, "s1")

    await execute_tools(state)
    # tool_a가 tool_b보다 먼저 호출
    assert call_order.index("tool_a") < call_order.index("tool_b")


@pytest.mark.asyncio
async def test_trace_integration():
    """Tool 실행이 RequestTrace에 기록되는지 검증."""
    from src.agent.nodes import create_execute_tools

    registry = AsyncMock()
    registry.execute = AsyncMock(return_value=ToolResult(
        success=True, data=[{"chunk_id": "c1", "content": "c", "score": 0.9}],
    ))

    execute_tools = create_execute_tools(registry)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "test"})]],
    )
    state = create_initial_state("test", plan, "s1")
    trace = RequestTrace(request_id="r1")
    state["trace"] = trace

    await execute_tools(state)

    tool_nodes = [n for n in trace.nodes if n.node.startswith("tool:")]
    assert len(tool_nodes) == 1
    assert tool_nodes[0].node == "tool:rag_search"
    assert tool_nodes[0].data["success"] is True
    assert tool_nodes[0].duration_ms > 0
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_parallel_tools.py -x -v`
Expected: FAIL (nodes.py가 아직 순차 실행)

- [ ] **Step 3: nodes.py 병렬 실행으로 변경**

`src/agent/nodes.py`의 `create_execute_tools` 함수를 다음으로 교체:

```python
import asyncio

def create_execute_tools(registry: ToolRegistry) -> Callable:
    """Tool 병렬 실행 노드. tool_groups별 asyncio.gather."""

    async def _execute_single(
        tool_call, context, scope, trace,
    ):
        """단일 Tool 실행 + trace 기록."""
        node = trace.start_node(f"tool:{tool_call.tool_name}") if trace else None
        try:
            result = await registry.execute(
                tool_name=tool_call.tool_name,
                params=tool_call.params,
                context=context,
                scope=scope,
            )
            if node:
                node.finish(
                    success=result.success,
                    chunks=len(result.data) if result.data else 0,
                )
            return tool_call, result
        except Exception as e:
            if node:
                node.finish(success=False, error=str(e))
            raise

    async def execute_tools(state: AgentState) -> dict:
        plan = state["plan"]
        question = state["question"]
        context = AgentContext(session_id=state["session_id"])
        trace = state.get("trace")
        search_results = []
        tools_called = []
        tool_latencies = []

        for group in plan.tool_groups:
            tasks = [
                _execute_single(tc, context, plan.scope, trace)
                for tc in group
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for tc, outcome in zip(group, results):
                if isinstance(outcome, Exception):
                    logger.warning("tool_failed", tool=tc.tool_name, error=str(outcome))
                    tool_latencies.append({
                        "tool": tc.tool_name, "success": False,
                        "chunks_found": 0, "ms": 0,
                    })
                    continue

                _tc, result = outcome
                tools_called.append(tc.tool_name)
                chunks_found = len(result.data) if result.success and result.data else 0
                if result.success and result.data:
                    search_results.extend(result.data)

                # trace에 이미 기록됨; tool_latencies는 하위 호환용
                node_ms = 0
                if trace:
                    matching = [n for n in trace.nodes if n.node == f"tool:{tc.tool_name}"]
                    if matching:
                        node_ms = matching[-1].duration_ms
                tool_latencies.append({
                    "tool": tc.tool_name, "success": result.success,
                    "chunks_found": chunks_found, "ms": round(node_ms, 1),
                })
                logger.info(
                    "tool_execute", tool=tc.tool_name,
                    success=result.success, chunks_found=chunks_found,
                    latency_ms=round(node_ms, 1),
                )

        return {
            "search_results": search_results,
            "tools_called": tools_called,
            "tool_latencies": tool_latencies,
        }

    return execute_tools
```

AgentState에 `trace` 필드 추가 (`src/agent/state.py`):
```python
# 추가
trace: RequestTrace | None  # Optional trace 참조
```

`create_initial_state`에도 trace 파라미터 추가.

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_parallel_tools.py -x -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/agent/nodes.py src/agent/state.py tests/test_parallel_tools.py
git commit -m "feat: Tool 병렬 실행 (asyncio.gather) + RequestTrace 통합"
```

---

### Task 3: StrategyBuilder + 소비자 코드 일괄 수정

**Files:**
- Modify: `src/router/strategy_builder.py:88-91` — tools → tool_groups
- Modify: `src/router/ai_router.py:106` — plan.tools → plan.tool_groups 로깅
- Modify: `src/agent/graph_executor.py:394,461` — AGENTIC 모드 plan.tools → plan.tool_groups
- Modify: `tests/test_graphs.py` — tools=[FakeTool()] → tool_groups=[[ToolCall(...)]]

- [ ] **Step 1: strategy_builder.py 수정**

`src/router/strategy_builder.py:88-102` 변경:

```python
# 변경 전 (line 91)
tools=tools,

# 변경 후
tool_groups=self._build_tool_groups(query, tools, strategy),
```

`_build_tool_groups` 메서드 추가:

```python
@staticmethod
def _build_tool_groups(query, tools, strategy):
    """도구 목록을 병렬 그룹으로 배정. 기본: 한 그룹 = 전부 병렬."""
    from src.router.execution_plan import ToolCall
    if not strategy.needs_rag or not tools:
        return []
    calls = [ToolCall(tool_name=t.name, params={"query": query}) for t in tools]
    return [calls]
```

build() 메서드 시그니처에 `query: str` 파라미터 추가.

- [ ] **Step 2: ai_router.py 수정**

```python
# 변경 전 (line 106)
tools_count=len(plan.tools),

# 변경 후
tools_count=sum(len(g) for g in plan.tool_groups),
```

- [ ] **Step 3: graph_executor.py AGENTIC 모드 수정**

AGENTIC 모드에서 plan.tools → plan.tool_groups에서 tool 인스턴스 추출:

```python
# graph_executor.py의 _execute_agentic, _stream_agentic에서
# 변경 전
lc_tools = convert_tools_to_langchain(plan.tools, context, plan.scope)

# 변경 후 — ToolCall에서 registry로 tool 인스턴스 조회
tool_instances = [
    self._registry.get(tc.tool_name)
    for group in plan.tool_groups
    for tc in group
    if self._registry.get(tc.tool_name)
]
lc_tools = convert_tools_to_langchain(tool_instances, context, plan.scope)
```

ToolRegistry에 `get()` 메서드 추가 (없으면):
```python
def get(self, name: str):
    return self._tools.get(name)
```

- [ ] **Step 4: test_graphs.py 수정**

FakeTool + tools=[FakeTool()] 패턴을 ToolCall로 변경:

```python
from src.router.execution_plan import ToolCall

# 변경 전
tools=[FakeTool()],

# 변경 후
tool_groups=[[ToolCall("rag_search", {"query": "대인배상 한도가 얼마야?"})]],
```

FakeTool 클래스 제거 (더 이상 불필요).

- [ ] **Step 5: ai_router.py build() 호출에 query 추가**

```python
# ai_router.py에서 strategy_builder.build() 호출 시 query 파라미터 추가
plan = self._strategy_builder.build(
    profile=profile,
    question_type=question_type,
    strategy=strategy,
    mode=mode,
    tools=tools,
    query=question,  # 추가
    history=history,
    ...
)
```

- [ ] **Step 6: 회귀 테스트**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/test_kms_client.py -x -v`
Expected: 전체 통과

- [ ] **Step 7: 커밋**

```bash
git add src/router/ src/agent/ tests/
git commit -m "feat: 소비자 코드 tools → tool_groups 일괄 마이그레이션"
```

---

### Task 4: graph_executor.py 스트리밍 trace 업데이트

**Files:**
- Modify: `src/agent/graph_executor.py:312-326` — 스트리밍에서 tool_latencies → trace 이벤트
- Modify: `src/agent/graph_executor.py:306-308` — create_initial_state에 trace 전달

- [ ] **Step 1: graph_executor.py 스트리밍 수정**

`_stream_deterministic`에서 trace를 state에 주입:

```python
# 변경 전 (line 306-308)
initial_state = create_initial_state(
    question, plan, session_id, is_streaming=True,
)

# 변경 후
initial_state = create_initial_state(
    question, plan, session_id, is_streaming=True, trace=trace,
)
```

`_execute_deterministic`도 동일 변경:
```python
initial_state = create_initial_state(question, plan, session_id, trace=trace)
```

execute/execute_stream에서 trace 전달이 이미 파라미터로 받고 있으므로 그대로 사용.

- [ ] **Step 2: 회귀 테스트**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/test_kms_client.py -x -v`
Expected: 전체 통과

- [ ] **Step 3: 커밋**

```bash
git add src/agent/graph_executor.py src/agent/state.py
git commit -m "feat: 스트리밍 모드에서 RequestTrace를 AgentState로 전달"
```

---

### Task 5: Docker 빌드 + 회귀 검증

- [ ] **Step 1: 전체 테스트**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/test_kms_client.py -x -v`
Expected: 전체 통과

- [ ] **Step 2: Docker 빌드**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && docker compose build api`

- [ ] **Step 3: 최종 커밋 (필요 시)**

---

## 검증 방법

```bash
# 1. 전체 테스트
.venv/bin/python -m pytest tests/ --ignore=tests/test_kms_client.py -x -v

# 2. 병렬 실행 테스트만
.venv/bin/python -m pytest tests/test_parallel_tools.py -x -v

# 3. 기존 그래프 테스트
.venv/bin/python -m pytest tests/test_graphs.py -x -v
```
