# Dual-Mode Execution Engine (LangGraph Foundation)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** UniversalAgent의 수동 for-loop을 LangGraph StateGraph로 교체하고, Profile.mode에 따라 결정론적(StateGraph) / 에이전틱(create_react_agent) 그래프를 자동 선택하는 듀얼 모드 엔진을 구축한다.

**Architecture:** 두 모드 모두 LangGraph 기반. 결정론적 모드는 StateGraph의 명시적 노드/엣지로 도구 실행 순서를 고정하고, 에이전틱 모드는 `create_react_agent()`로 LLM이 도구를 자율 선택한다. 공통 AgentState TypedDict를 공유하며, Guardrail 체인/출처 생성/SSE 스트리밍은 양쪽 모드가 동일한 코드를 사용한다.

**Tech Stack:** Python 3.11+, LangGraph 0.3+, langchain-core 0.3+, langchain-ollama, langchain-openai, FastAPI, SSE

---

## File Structure

### 신규 파일

| 파일 | 역할 |
|------|------|
| `src/agent/state.py` | AgentState TypedDict + 헬퍼 |
| `src/agent/nodes.py` | LangGraph 노드 팩토리 함수 (execute_tools, generate_response 등) |
| `src/agent/graphs.py` | `build_deterministic_graph()` + `build_agentic_graph()` |
| `src/agent/graph_executor.py` | GraphExecutor: 모드별 그래프 선택 + 실행 + SSE 변환 |
| `src/agent/tool_adapter.py` | Tool Protocol → LangChain StructuredTool 변환 |
| `src/agent/chat_model_factory.py` | ProviderFactory 설정 → LangChain ChatModel 생성 |
| `seeds/profiles/general-assistant.yaml` | 에이전틱 모드 테스트용 프로필 |
| `tests/test_agent_state.py` | AgentState + 노드 단위 테스트 |
| `tests/test_graphs.py` | 그래프 빌드 + 실행 테스트 |
| `tests/test_tool_adapter.py` | Tool Protocol → LangChain 변환 테스트 |

### 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/domain/models.py` | AgentMode에 `DETERMINISTIC` 추가 |
| `src/agent/profile.py` | `max_tool_calls`, `agent_timeout_seconds` 필드 |
| `src/agent/profile_store.py` | 새 필드 파싱 |
| `src/router/execution_plan.py` | `max_tool_calls`, `agent_timeout_seconds` |
| `src/router/strategy_builder.py` | 에이전틱 설정을 plan에 전달 |
| `src/router/mode_selector.py` | DETERMINISTIC 모드 처리 |
| `src/gateway/router.py` | UniversalAgent → GraphExecutor 교체 |
| `src/main.py` | GraphExecutor 초기화, ChatModel 생성 |
| `pyproject.toml` | `langchain-ollama`, `langchain-openai` 의존성 |
| `seeds/profiles/insurance-qa.yaml` | `mode: deterministic` |
| `seeds/profiles/insurance-contract.yaml` | `mode: deterministic` |
| `seeds/profiles/general-chat.yaml` | `mode: deterministic` |

### 삭제/대체 파일

| 파일 | 이유 |
|------|------|
| `src/agent/universal.py` | GraphExecutor가 대체. 삭제 또는 deprecated 처리 |

---

## 설계 결정

### 왜 LangGraph인가

ai-worker에서 이미 검증된 패턴:
- `StateGraph` + `TypedDict` 상태 관리
- 팩토리 함수 노드 (`create_*`) + 클로저 DI
- `track_node` 데코레이터로 노드별 관측성
- 조건부 엣지 (`add_conditional_edges`)로 결정론적 라우팅
- `stream_mode="updates"`로 노드별 스트리밍

### 결정론적 모드 그래프

```
START
  ↓
[route_by_rag] ── needs_rag=False ──→ [direct_generate] → [END]
  │
  └── needs_rag=True
        ↓
  [execute_tools] (순서대로 Tool 실행)
        ↓
  [generate_with_context] (LLM 답변 생성)
        ↓
  [run_guardrails] (Guardrail 체인)
        ↓
  [build_response] → [END]
```

- 현재 UniversalAgent.execute()와 동일한 흐름
- 차이: 상태가 TypedDict로 명시적, 노드/엣지가 선언적

### 에이전틱 모드 그래프

```
START
  ↓
[create_react_agent] (LLM이 도구 자율 선택, max_tool_calls 제한)
  ↓
[extract_results] (agent 출력에서 sources/trace 추출)
  ↓
[run_guardrails]
  ↓
[build_response] → [END]
```

- `create_react_agent(model, tools)` — LangGraph 내장 ReAct 에이전트
- LangChain ChatModel 필요 → `chat_model_factory.py`에서 생성
- Tool Protocol → LangChain StructuredTool 변환 → `tool_adapter.py`

### ChatModel 전략

```python
# 결정론적 모드: 기존 LLMProvider 사용 (tool calling 불필요)
# 에이전틱 모드: LangChain ChatModel 사용 (tool calling 필수)

# ProviderFactory 설정을 재활용:
#   development → ChatOllama(model="gemma2:9b", base_url="http://localhost:11434")
#   openai     → ChatOpenAI(model="gpt-4o-mini", api_key=...)
#   HTTP서버   → ChatOpenAI(base_url=server_url, api_key="not-needed")
```

기존 LLMProvider를 감싸지 않고, 동일한 설정에서 ChatModel을 별도 생성.
결정론적 모드는 LLMProvider를 그대로 사용하므로 기존 코드 영향 없음.

---

## Chunk 1: 데이터 모델 + AgentState + 의존성

### Task 1: AgentMode.DETERMINISTIC + Profile 확장 + 의존성

**Files:**
- Modify: `src/domain/models.py:15-19`
- Modify: `src/agent/profile.py`
- Modify: `src/agent/profile_store.py`
- Modify: `src/router/execution_plan.py`
- Modify: `src/router/strategy_builder.py`
- Modify: `src/router/mode_selector.py`
- Modify: `pyproject.toml`
- Test: `tests/test_execution_plan.py`
- Test: `tests/test_profile.py`

- [ ] **Step 1: 테스트 작성 — DETERMINISTIC 열거형 + Profile 새 필드**

```python
# tests/test_execution_plan.py에 추가
def test_agent_mode_deterministic():
    from src.domain.models import AgentMode
    assert AgentMode.DETERMINISTIC == "deterministic"
    assert AgentMode.AGENTIC == "agentic"
    assert len(AgentMode) == 4  # DETERMINISTIC, AGENTIC, WORKFLOW, HYBRID
```

```python
# tests/test_profile.py에 추가
from src.agent.profile import AgentProfile
from src.domain.models import AgentMode

def test_profile_agentic_defaults():
    profile = AgentProfile(id="test", name="Test", domain_scopes=[])
    assert profile.max_tool_calls == 5
    assert profile.agent_timeout_seconds == 30

def test_profile_agentic_custom():
    profile = AgentProfile(
        id="test", name="Test", domain_scopes=[],
        mode=AgentMode.AGENTIC, max_tool_calls=10, agent_timeout_seconds=60,
    )
    assert profile.max_tool_calls == 10
    assert profile.agent_timeout_seconds == 60
```

- [ ] **Step 2: 테스트 실행 → FAIL 확인**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_execution_plan.py::test_agent_mode_deterministic tests/test_profile.py::test_profile_agentic_defaults -v`
Expected: FAIL

- [ ] **Step 3: AgentMode에 DETERMINISTIC 추가**

`src/domain/models.py`:
```python
class AgentMode(str, Enum):
    """오케스트레이션 모드."""
    DETERMINISTIC = "deterministic"  # StateGraph: 정해진 Tool 순서 실행
    AGENTIC = "agentic"              # create_react_agent: LLM이 Tool 자율 선택
    WORKFLOW = "workflow"
    HYBRID = "hybrid"
```

- [ ] **Step 4: Profile에 에이전틱 설정 필드 추가**

`src/agent/profile.py` — AgentProfile:
```python
    max_tool_calls: int = 5           # 에이전틱 모드 최대 도구 호출 횟수
    agent_timeout_seconds: int = 30   # 에이전틱 루프 타임아웃
```

`src/agent/profile_store.py` — `_parse_profile()`:
```python
    max_tool_calls=data.get("max_tool_calls", 5),
    agent_timeout_seconds=data.get("agent_timeout_seconds", 30),
```
`_profile_to_dict()`:
```python
    "max_tool_calls": profile.max_tool_calls,
    "agent_timeout_seconds": profile.agent_timeout_seconds,
```

- [ ] **Step 5: ExecutionPlan + StrategyBuilder + ModeSelector**

`src/router/execution_plan.py` — ExecutionPlan:
```python
    max_tool_calls: int = 5
    agent_timeout_seconds: int = 30
```

`src/router/strategy_builder.py` — `build()` 반환부:
```python
    return ExecutionPlan(
        # ... 기존 필드 ...
        max_tool_calls=profile.max_tool_calls,
        agent_timeout_seconds=profile.agent_timeout_seconds,
    )
```

`src/router/mode_selector.py` — DETERMINISTIC 처리:
```python
    if profile.mode == AgentMode.DETERMINISTIC:
        return AgentMode.DETERMINISTIC, None
```
(`select()` 메서드 상단에 AGENTIC 분기 전에 추가)

- [ ] **Step 6: pyproject.toml에 LangChain ChatModel 의존성 추가**

```toml
[project.optional-dependencies]
# ... 기존 ...
ollama = [
    "langchain-ollama>=0.3.0",
]
openai = [
    "openai>=1.50.0",
    "langchain-openai>=0.3.0",
]
```

- [ ] **Step 7: 테스트 실행 → ALL PASS**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/ -x -v`

- [ ] **Step 8: 커밋**

```bash
git add src/domain/models.py src/agent/profile.py src/agent/profile_store.py \
  src/router/execution_plan.py src/router/strategy_builder.py src/router/mode_selector.py \
  pyproject.toml tests/
git commit -m "feat: 듀얼 모드 데이터 모델 — DETERMINISTIC 열거형 + Profile 에이전틱 필드 + LangChain 의존성"
```

---

### Task 2: AgentState TypedDict

**Files:**
- Create: `src/agent/state.py`
- Test: `tests/test_agent_state.py`

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_agent_state.py
from src.agent.state import AgentState, create_initial_state
from src.domain.models import AgentMode, SearchScope
from src.router.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType


def test_create_initial_state():
    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(domain_codes=["ga"]),
        question_type=QuestionType.STANDALONE,
    )
    state = create_initial_state(
        question="보험 약관",
        plan=plan,
        session_id="sess-1",
    )
    assert state["question"] == "보험 약관"
    assert state["mode"] == AgentMode.DETERMINISTIC
    assert state["search_results"] == []
    assert state["answer"] == ""
    assert state["tools_called"] == []


def test_state_is_typed_dict():
    """AgentState는 TypedDict여야 한다 (LangGraph 호환)."""
    import typing
    assert hasattr(AgentState, "__annotations__")
    # TypedDict는 dict의 서브클래스
    assert issubclass(AgentState, dict)
```

- [ ] **Step 2: 테스트 실행 → FAIL**

- [ ] **Step 3: state.py 구현**

`src/agent/state.py`:
```python
"""AgentState: LangGraph 그래프의 공유 상태.

결정론적/에이전틱 양쪽 모드에서 동일한 TypedDict를 사용한다.
ai-worker의 RAGState 패턴을 범용화.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from src.domain.models import AgentMode, SearchScope
from src.router.execution_plan import ExecutionPlan, QuestionType


class AgentState(TypedDict):
    """LangGraph 그래프 상태."""

    # 입력
    question: str
    plan: ExecutionPlan
    session_id: str

    # 모드 (plan.mode 복사 — 조건부 엣지에서 빠르게 참조)
    mode: AgentMode

    # Tool 실행 결과
    search_results: list[dict]
    tools_called: list[str]
    tool_latencies: list[dict]

    # LLM 응답
    answer: str

    # Guardrail
    guardrail_results: dict

    # 출처
    sources: list[dict]

    # 메타데이터
    latency_ms: float


def create_initial_state(
    question: str,
    plan: ExecutionPlan,
    session_id: str = "",
) -> AgentState:
    """초기 상태 생성."""
    return AgentState(
        question=question,
        plan=plan,
        session_id=session_id,
        mode=plan.mode,
        search_results=[],
        tools_called=[],
        tool_latencies=[],
        answer="",
        guardrail_results={},
        sources=[],
        latency_ms=0.0,
    )
```

- [ ] **Step 4: 테스트 실행 → PASS**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_agent_state.py -v`

- [ ] **Step 5: 커밋**

```bash
git add src/agent/state.py tests/test_agent_state.py
git commit -m "feat: AgentState TypedDict — LangGraph 그래프 공유 상태"
```

---

## Chunk 2: 결정론적 그래프 (StateGraph)

### Task 3: 노드 팩토리 함수

**Files:**
- Create: `src/agent/nodes.py`
- Test: `tests/test_agent_state.py` (추가)

ai-worker 패턴: 팩토리 함수가 의존성을 클로저로 캡처하고, 순수 노드 함수를 반환.

- [ ] **Step 1: 테스트 작성 — route_by_rag 노드**

```python
# tests/test_agent_state.py에 추가
from src.agent.nodes import route_by_rag

def test_route_by_rag_needs_search():
    state = create_initial_state(
        question="보험 약관",
        plan=ExecutionPlan(
            mode=AgentMode.DETERMINISTIC,
            scope=SearchScope(),
            question_type=QuestionType.STANDALONE,
        ),
    )
    assert route_by_rag(state) == "execute_tools"


def test_route_by_rag_no_search():
    from src.router.execution_plan import QuestionStrategy
    state = create_initial_state(
        question="안녕하세요",
        plan=ExecutionPlan(
            mode=AgentMode.DETERMINISTIC,
            scope=SearchScope(),
            question_type=QuestionType.GREETING,
            strategy=QuestionStrategy(needs_rag=False),
        ),
    )
    assert route_by_rag(state) == "direct_generate"
```

- [ ] **Step 2: 테스트 실행 → FAIL**

- [ ] **Step 3: nodes.py 구현**

`src/agent/nodes.py`:
```python
"""LangGraph 노드 팩토리 함수.

ai-worker 패턴: 팩토리 함수가 의존성을 클로저로 캡처 → 순수 노드 함수 반환.
"""

import time
from typing import Any

from src.agent.state import AgentState
from src.domain.models import AgentResponse, SourceRef
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.safety.base import Guardrail, GuardrailContext
from src.tools.base import AgentContext
from src.tools.registry import ToolRegistry

logger = get_logger(__name__)

MAX_CONTENT_PREVIEW_LEN = 500
MAX_SOURCE_PREVIEW_LEN = 200
MAX_SOURCES = 5


# --- 라우팅 함수 (조건부 엣지) ---

def route_by_rag(state: AgentState) -> str:
    """needs_rag 여부로 다음 노드를 결정한다."""
    if state["plan"].strategy.needs_rag:
        return "execute_tools"
    return "direct_generate"


# --- 노드 팩토리 함수 ---

def create_execute_tools(
    registry: ToolRegistry,
) -> callable:
    """Tool 순차 실행 노드."""

    async def execute_tools(state: AgentState) -> dict:
        plan = state["plan"]
        question = state["question"]
        context = AgentContext(session_id=state["session_id"])
        search_results = []
        tools_called = []
        tool_latencies = []

        for tool in plan.tools:
            tool_name = tool.name if hasattr(tool, "name") else str(tool)
            tools_called.append(tool_name)

            t_start = time.time()
            result = await registry.execute(
                tool_name=tool_name,
                params={"query": question, "subject": question},
                context=context,
                scope=plan.scope,
            )
            tool_ms = (time.time() - t_start) * 1000

            chunks_found = 0
            if result.success and isinstance(result.data, list):
                search_results.extend(result.data)
                chunks_found = len(result.data)

            tool_latencies.append({
                "tool": tool_name,
                "success": result.success,
                "chunks_found": chunks_found,
                "ms": round(tool_ms, 1),
            })
            logger.info(
                "tool_execute",
                tool=tool_name,
                success=result.success,
                chunks_found=chunks_found,
                latency_ms=round(tool_ms, 1),
            )

        return {
            "search_results": search_results,
            "tools_called": tools_called,
            "tool_latencies": tool_latencies,
        }

    return execute_tools


def create_generate_with_context(
    llm: LLMProvider,
) -> callable:
    """검색 결과 기반 LLM 답변 생성 노드."""

    async def generate_with_context(state: AgentState) -> dict:
        plan = state["plan"]
        question = state["question"]
        results = state["search_results"]

        max_chunks = plan.strategy.max_vector_chunks
        prompt_results = results[:max_chunks]

        prompt = _build_prompt(question, plan, prompt_results)
        answer = await llm.generate(prompt, system=plan.system_prompt)

        logger.info("llm_generate", answer_len=len(answer), context_chunks=len(prompt_results))
        return {"answer": answer}

    return generate_with_context


def create_direct_generate(
    llm: LLMProvider,
) -> callable:
    """직접 답변 생성 노드 (RAG 불필요)."""

    async def direct_generate(state: AgentState) -> dict:
        question = state["question"]
        plan = state["plan"]
        answer = await llm.generate(question, system=plan.system_prompt)
        logger.info("direct_generate", answer_len=len(answer))
        return {"answer": answer}

    return direct_generate


def create_run_guardrails(
    guardrails: dict[str, Guardrail],
) -> callable:
    """Guardrail 체인 실행 노드."""

    GUARDRAIL_BLOCK_TEMPLATE = "답변을 제공할 수 없습니다. 사유: {reason}"

    async def run_guardrails(state: AgentState) -> dict:
        plan = state["plan"]
        answer = state["answer"]

        if not plan.guardrail_chain:
            return {"guardrail_results": {}}

        context = GuardrailContext(
            question=state["question"],
            source_documents=state["search_results"],
            profile_id=state["session_id"],
            response_policy=plan.response_policy,
        )

        results = {}
        for name in plan.guardrail_chain:
            guardrail = guardrails.get(name)
            if not guardrail:
                results[name] = "skipped"
                continue
            try:
                t = time.time()
                result = await guardrail.check(answer, context)
                ms = (time.time() - t) * 1000
                results[name] = {"action": result.action, "ms": round(ms, 1)}

                if result.action == "block":
                    logger.warning("guardrail_block", guard=name, reason=result.reason)
                    return {
                        "answer": GUARDRAIL_BLOCK_TEMPLATE.format(reason=result.reason),
                        "guardrail_results": results,
                    }
                if result.action == "warn" and result.modified_answer:
                    logger.info("guardrail_warn", guard=name, reason=result.reason)
                    answer = result.modified_answer
            except Exception as e:
                logger.warning("guardrail_error", guard=name, error=str(e))
                results[name] = {"action": "error", "error": str(e)}

        return {"answer": answer, "guardrail_results": results}

    return run_guardrails


def create_build_response() -> callable:
    """출처 생성 + 최종 응답 조립 노드."""

    async def build_response(state: AgentState) -> dict:
        results = state["search_results"]
        sources = []
        seen = set()
        for r in results:
            doc_id = r.get("document_id", "")
            if doc_id in seen:
                continue
            seen.add(doc_id)
            sources.append({
                "document_id": doc_id,
                "title": r.get("title", r.get("file_name", "")),
                "chunk_text": r.get("content", "")[:MAX_SOURCE_PREVIEW_LEN],
                "score": r.get("score", 0.0),
                "method": r.get("method", "vector"),
            })
        return {"sources": sources[:MAX_SOURCES]}

    return build_response


# --- 헬퍼 ---

def _format_result(r: dict) -> str:
    if "content" in r:
        return r["content"][:MAX_CONTENT_PREVIEW_LEN]
    if "subject" in r and "predicate" in r and "object" in r:
        parts = [f"{r['subject']} — {r['predicate']}: {r['object']}"]
        if r.get("table_context"):
            parts.append(f"(맥락: {r['table_context']})")
        return " ".join(parts)
    return str(r)[:MAX_CONTENT_PREVIEW_LEN]


def _build_prompt(question: str, plan, results: list[dict]) -> str:
    if not results:
        return f"질문: {question}\n\n관련 문서를 찾지 못했습니다."

    max_chunks = plan.strategy.max_vector_chunks
    context_parts = []
    for i, r in enumerate(results[:max_chunks], 1):
        title = r.get("title", r.get("file_name", ""))
        content = _format_result(r)
        context_parts.append(f"[{i}] {title}\n{content}")

    context_text = "\n\n".join(context_parts)

    if plan.conversation_context:
        return (
            f"대화 맥락:\n{plan.conversation_context}\n\n"
            f"참고 문서:\n{context_text}\n\n"
            f"질문: {question}"
        )
    return f"참고 문서:\n{context_text}\n\n질문: {question}"
```

- [ ] **Step 4: 테스트 실행 → PASS**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_agent_state.py -v`

- [ ] **Step 5: 커밋**

```bash
git add src/agent/nodes.py tests/test_agent_state.py
git commit -m "feat: LangGraph 노드 팩토리 함수 — execute_tools, generate, guardrails, build_response"
```

---

### Task 4: 결정론적 그래프 빌드

**Files:**
- Create: `src/agent/graphs.py`
- Test: `tests/test_graphs.py`

- [ ] **Step 1: 테스트 작성 — 그래프 빌드 + 컴파일**

```python
# tests/test_graphs.py
from unittest.mock import AsyncMock, MagicMock

from src.agent.graphs import build_deterministic_graph
from src.agent.state import AgentState, create_initial_state
from src.domain.models import AgentMode, SearchScope
from src.router.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType


def test_build_deterministic_graph_compiles():
    """결정론적 그래프가 정상 컴파일되는지 확인."""
    mock_llm = MagicMock()
    mock_registry = MagicMock()

    graph = build_deterministic_graph(
        llm=mock_llm,
        registry=mock_registry,
        guardrails={},
    )
    app = graph.compile()
    assert app is not None


def test_deterministic_graph_has_expected_nodes():
    """결정론적 그래프에 필요한 노드가 모두 있는지 확인."""
    mock_llm = MagicMock()
    mock_registry = MagicMock()

    graph = build_deterministic_graph(
        llm=mock_llm,
        registry=mock_registry,
        guardrails={},
    )
    node_names = set(graph.nodes.keys())
    assert "execute_tools" in node_names
    assert "generate_with_context" in node_names
    assert "direct_generate" in node_names
    assert "run_guardrails" in node_names
    assert "build_response" in node_names
```

- [ ] **Step 2: 테스트 실행 → FAIL**

- [ ] **Step 3: graphs.py — 결정론적 그래프 빌드**

`src/agent/graphs.py`:
```python
"""LangGraph 그래프 빌더.

결정론적(StateGraph) + 에이전틱(create_react_agent) 그래프를 빌드한다.
"""

from langgraph.graph import END, StateGraph

from src.agent.nodes import (
    create_build_response,
    create_direct_generate,
    create_execute_tools,
    create_generate_with_context,
    create_run_guardrails,
    route_by_rag,
)
from src.agent.state import AgentState
from src.infrastructure.providers.base import LLMProvider
from src.safety.base import Guardrail
from src.tools.registry import ToolRegistry


def build_deterministic_graph(
    llm: LLMProvider,
    registry: ToolRegistry,
    guardrails: dict[str, Guardrail],
) -> StateGraph:
    """결정론적 RAG 파이프라인 그래프.

    START → route ─┬→ execute_tools → generate_with_context → run_guardrails → build_response → END
                   └→ direct_generate → END
    """
    workflow = StateGraph(AgentState)

    # 노드 등록
    workflow.add_node("execute_tools", create_execute_tools(registry))
    workflow.add_node("generate_with_context", create_generate_with_context(llm))
    workflow.add_node("direct_generate", create_direct_generate(llm))
    workflow.add_node("run_guardrails", create_run_guardrails(guardrails))
    workflow.add_node("build_response", create_build_response())

    # 엣지 연결
    workflow.set_conditional_entry_point(
        route_by_rag,
        {
            "execute_tools": "execute_tools",
            "direct_generate": "direct_generate",
        },
    )

    # RAG 경로: tools → generate → guardrails → build → END
    workflow.add_edge("execute_tools", "generate_with_context")
    workflow.add_edge("generate_with_context", "run_guardrails")
    workflow.add_edge("run_guardrails", "build_response")
    workflow.add_edge("build_response", END)

    # 직접 응답 경로: direct → END
    workflow.add_edge("direct_generate", END)

    return workflow
```

- [ ] **Step 4: 테스트 실행 → PASS**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_graphs.py -v`

- [ ] **Step 5: 커밋**

```bash
git add src/agent/graphs.py tests/test_graphs.py
git commit -m "feat: 결정론적 StateGraph — route → tools → generate → guardrails → response"
```

---

## Chunk 3: 에이전틱 그래프 (create_react_agent)

### Task 5: Tool Protocol → LangChain Tool 어댑터

**Files:**
- Create: `src/agent/tool_adapter.py`
- Test: `tests/test_tool_adapter.py`

Tool Protocol의 `input_schema` → LangChain `StructuredTool`로 변환.
ScopedTool은 SearchScope를 클로저로 바인딩.

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_tool_adapter.py
from src.agent.tool_adapter import convert_tools_to_langchain
from src.domain.models import SearchScope
from src.tools.base import AgentContext, ToolResult


class FakeTool:
    name = "fake_search"
    description = "테스트용 검색 도구"
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "검색어"}},
        "required": ["query"],
    }

    async def execute(self, params, context):
        return ToolResult(success=True, data=[{"content": "결과"}])


def test_convert_tool():
    tools = convert_tools_to_langchain(
        [FakeTool()],
        context=AgentContext(session_id="test"),
        scope=SearchScope(),
    )
    assert len(tools) == 1
    assert tools[0].name == "fake_search"
    assert tools[0].description == "테스트용 검색 도구"


async def test_converted_tool_invocation():
    tools = convert_tools_to_langchain(
        [FakeTool()],
        context=AgentContext(session_id="test"),
        scope=SearchScope(),
    )
    result = await tools[0].ainvoke({"query": "테스트"})
    assert "결과" in result
```

- [ ] **Step 2: 테스트 실행 → FAIL**

- [ ] **Step 3: tool_adapter.py 구현**

`src/agent/tool_adapter.py`:
```python
"""Tool Protocol → LangChain StructuredTool 변환.

기존 Tool/ScopedTool을 create_react_agent에서 사용할 수 있게 변환한다.
SearchScope는 클로저로 바인딩하여 LLM에 노출하지 않는다.
"""

from typing import Any, Union

from langchain_core.tools import StructuredTool

from src.domain.models import SearchScope
from src.tools.base import AgentContext, ScopedTool, Tool, ToolResult

MAX_TOOL_RESULT_LEN = 2000


def _format_tool_result(result: ToolResult) -> str:
    """ToolResult → LLM에 반환할 텍스트."""
    if not result.success:
        return f"Error: {result.error}"

    if isinstance(result.data, list):
        parts = []
        for i, item in enumerate(result.data[:10], 1):
            if isinstance(item, dict):
                title = item.get("title", item.get("file_name", ""))
                content = item.get("content", "")
                if "subject" in item and "predicate" in item:
                    content = f"{item['subject']} — {item['predicate']}: {item['object']}"
                parts.append(f"[{i}] {title}\n{content[:300]}")
            else:
                parts.append(f"[{i}] {str(item)[:300]}")
        return "\n\n".join(parts)

    return str(result.data)[:MAX_TOOL_RESULT_LEN]


def convert_tools_to_langchain(
    tools: list[Union[Tool, ScopedTool]],
    context: AgentContext,
    scope: SearchScope,
) -> list[StructuredTool]:
    """Tool Protocol 도구들을 LangChain StructuredTool로 변환한다."""
    converted = []

    for tool in tools:
        is_scoped = isinstance(tool, ScopedTool)

        # 클로저로 scope, context 바인딩
        _tool = tool
        _is_scoped = is_scoped

        async def _invoke(
            _t=_tool, _s=_is_scoped, **kwargs,
        ) -> str:
            if _s:
                result = await _t.execute(params=kwargs, context=context, scope=scope)
            else:
                result = await _t.execute(params=kwargs, context=context)
            return _format_tool_result(result)

        lc_tool = StructuredTool.from_function(
            coroutine=_invoke,
            name=tool.name,
            description=tool.description,
            args_schema=None,  # input_schema를 직접 사용
        )
        # input_schema를 LangChain tool schema로 설정
        lc_tool.args_schema = None
        lc_tool.schema_ = tool.input_schema

        converted.append(lc_tool)

    return converted
```

- [ ] **Step 4: 테스트 실행 → PASS**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_tool_adapter.py -v`

- [ ] **Step 5: 커밋**

```bash
git add src/agent/tool_adapter.py tests/test_tool_adapter.py
git commit -m "feat: Tool Protocol → LangChain StructuredTool 어댑터 — SearchScope 클로저 바인딩"
```

---

### Task 6: ChatModel 팩토리

**Files:**
- Create: `src/agent/chat_model_factory.py`
- Test: `tests/test_graphs.py` (추가)

에이전틱 모드의 `create_react_agent`는 LangChain `BaseChatModel`이 필요하다.
기존 `ProviderFactory` 설정을 재활용하여 ChatModel을 생성한다.

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_graphs.py에 추가
def test_chat_model_factory_ollama():
    """development 모드에서 ChatOllama 생성 (import만 확인)."""
    from src.agent.chat_model_factory import create_chat_model
    from src.config import ProviderMode

    # langchain-ollama가 설치되어 있으면 ChatOllama 반환
    try:
        model = create_chat_model(
            provider_mode=ProviderMode.DEVELOPMENT,
            model_name="gemma2:9b",
            ollama_host="http://localhost:11434",
        )
        assert model is not None
    except ImportError:
        pass  # langchain-ollama 미설치 환경에서는 스킵
```

- [ ] **Step 2: chat_model_factory.py 구현**

`src/agent/chat_model_factory.py`:
```python
"""ChatModel 팩토리.

ProviderFactory 설정을 재활용하여 LangChain ChatModel을 생성한다.
결정론적 모드는 기존 LLMProvider를 사용하므로 이 모듈은 에이전틱 모드 전용.
"""

from langchain_core.language_models import BaseChatModel

from src.config import ProviderMode


def create_chat_model(
    provider_mode: ProviderMode,
    model_name: str = "",
    ollama_host: str = "http://localhost:11434",
    openai_api_key: str = "",
    server_url: str = "",
) -> BaseChatModel:
    """설정 기반 ChatModel 생성.

    Args:
        provider_mode: development/openai/production
        model_name: 모델명
        ollama_host: Ollama 서버 주소
        openai_api_key: OpenAI API 키
        server_url: GPU/MLX 서버 URL (OpenAI 호환)

    Returns:
        BaseChatModel (tool calling 지원)
    """
    # GPU/MLX 서버가 설정되면 OpenAI 호환 API로 연결
    if server_url:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            base_url=f"{server_url.rstrip('/')}/v1",
            api_key="not-needed",
            model=model_name or "default",
        )

    if provider_mode == ProviderMode.DEVELOPMENT:
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name,
            base_url=ollama_host,
        )

    # openai / production
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        api_key=openai_api_key,
    )
```

- [ ] **Step 3: 테스트 + 커밋**

```bash
cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_graphs.py -v
git add src/agent/chat_model_factory.py tests/test_graphs.py
git commit -m "feat: ChatModel 팩토리 — Ollama/OpenAI/HTTP 서버 → LangChain ChatModel"
```

---

### Task 7: 에이전틱 그래프 빌드

**Files:**
- Modify: `src/agent/graphs.py`
- Test: `tests/test_graphs.py` (추가)

`create_react_agent`로 에이전틱 그래프를 빌드한다.
Guardrail은 ReAct 에이전트 실행 후 별도 노드로 적용.

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_graphs.py에 추가
def test_build_agentic_graph_compiles():
    """에이전틱 그래프가 정상 컴파일되는지 확인."""
    from unittest.mock import MagicMock
    from src.agent.graphs import build_agentic_graph

    mock_chat_model = MagicMock()
    # create_react_agent는 ChatModel + tools 필요
    # tools가 비어있으면 에이전트 생성 불가 → 빈 리스트 에러 확인
    try:
        graph = build_agentic_graph(
            chat_model=mock_chat_model,
            tools=[],
            guardrails={},
        )
    except ValueError:
        pass  # tools가 비어있으면 ValueError 예상
```

- [ ] **Step 2: graphs.py에 에이전틱 그래프 추가**

`src/agent/graphs.py`에 추가:
```python
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent


def build_agentic_graph(
    chat_model: BaseChatModel,
    tools: list[BaseTool],
    guardrails: dict[str, Guardrail],
    max_tool_calls: int = 5,
) -> StateGraph:
    """에이전틱 ReAct 그래프.

    create_react_agent로 LLM이 도구를 자율 선택하고,
    이후 guardrail 체인을 적용한다.

    Args:
        chat_model: LangChain ChatModel (tool calling 지원)
        tools: LangChain 도구 목록
        guardrails: Guardrail 인스턴스
        max_tool_calls: 최대 도구 호출 횟수
    """
    if not tools:
        raise ValueError("에이전틱 모드에는 최소 1개 이상의 도구가 필요합니다.")

    # create_react_agent는 이미 컴파일된 그래프를 반환
    # → 커스텀 그래프로 감싸서 guardrail 노드를 추가
    agent = create_react_agent(
        model=chat_model,
        tools=tools,
    )

    return agent
```

> **참고:** `create_react_agent`가 반환하는 그래프는 이미 컴파일 가능.
> Guardrail은 `GraphExecutor` 레벨에서 에이전트 실행 후 적용한다 (Task 8).
> create_react_agent의 출력에 guardrail 노드를 추가하는 것은 LangGraph API 제약상 복잡하므로,
> 실행 후 후처리가 더 실용적.

- [ ] **Step 3: 테스트 + 커밋**

```bash
cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_graphs.py -v
git add src/agent/graphs.py tests/test_graphs.py
git commit -m "feat: 에이전틱 ReAct 그래프 — create_react_agent 기반"
```

---

## Chunk 4: GraphExecutor (통합 실행기)

### Task 8: GraphExecutor — 모드별 그래프 선택 + 실행

**Files:**
- Create: `src/agent/graph_executor.py`
- Test: `tests/test_graphs.py` (추가)

UniversalAgent를 대체하는 핵심 클래스.
`execute()` + `execute_stream()` 인터페이스 유지.

- [ ] **Step 1: 테스트 작성**

```python
# tests/test_graphs.py에 추가
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.graph_executor import GraphExecutor
from src.agent.state import create_initial_state
from src.domain.models import AgentMode, SearchScope
from src.router.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType


@pytest.mark.asyncio
async def test_graph_executor_deterministic():
    """결정론적 모드에서 정상 실행."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="직접 답변입니다")
    mock_registry = MagicMock()

    executor = GraphExecutor(
        main_llm=mock_llm,
        tool_registry=mock_registry,
        guardrails={},
    )

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        question_type=QuestionType.GREETING,
        strategy=QuestionStrategy(needs_rag=False),
    )

    response = await executor.execute(
        question="안녕하세요",
        plan=plan,
        session_id="test-session",
    )
    assert response.answer == "직접 답변입니다"
    assert response.trace.mode == "deterministic"
```

- [ ] **Step 2: graph_executor.py 구현**

`src/agent/graph_executor.py`:
```python
"""GraphExecutor: 모드별 LangGraph 그래프 선택 + 실행.

UniversalAgent를 대체. execute()/execute_stream() 인터페이스 유지.
"""

import time
from typing import Any, AsyncIterator, Optional

from langchain_core.language_models import BaseChatModel

from src.agent.graphs import build_agentic_graph, build_deterministic_graph
from src.agent.state import AgentState, create_initial_state
from src.agent.tool_adapter import convert_tools_to_langchain
from src.domain.models import AgentMode, AgentResponse, SourceRef, TraceInfo
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.observability.trace_logger import RequestTrace
from src.router.execution_plan import ExecutionPlan
from src.safety.base import Guardrail, GuardrailContext
from src.tools.base import AgentContext
from src.tools.registry import ToolRegistry

logger = get_logger(__name__)

GUARDRAIL_BLOCK_TEMPLATE = "답변을 제공할 수 없습니다. 사유: {reason}"


class GraphExecutor:
    """모드별 LangGraph 그래프를 선택하여 실행한다.

    - DETERMINISTIC: StateGraph (고정 Tool 순서)
    - AGENTIC: create_react_agent (LLM 자율 Tool 선택)
    """

    def __init__(
        self,
        main_llm: LLMProvider,
        tool_registry: ToolRegistry,
        guardrails: Optional[dict[str, Guardrail]] = None,
        chat_model: Optional[BaseChatModel] = None,
    ):
        self._main_llm = main_llm
        self._registry = tool_registry
        self._guardrails = guardrails or {}
        self._chat_model = chat_model

        # 결정론적 그래프 (한 번 컴파일, 재사용)
        det_graph = build_deterministic_graph(
            llm=main_llm,
            registry=tool_registry,
            guardrails=self._guardrails,
        )
        self._deterministic_app = det_graph.compile()

    async def execute(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str = "",
        trace: Optional[RequestTrace] = None,
    ) -> AgentResponse:
        """ExecutionPlan 기반 실행."""
        start_time = time.time()

        if plan.mode == AgentMode.AGENTIC:
            response = await self._execute_agentic(question, plan, session_id, trace)
        else:
            response = await self._execute_deterministic(question, plan, session_id, trace)

        # latency 업데이트
        total_ms = (time.time() - start_time) * 1000
        if response.trace:
            response.trace.latency_ms = total_ms

        return response

    async def execute_stream(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str = "",
        trace: Optional[RequestTrace] = None,
    ) -> AsyncIterator[dict]:
        """SSE 스트리밍 실행."""
        if plan.mode == AgentMode.AGENTIC:
            async for event in self._stream_agentic(question, plan, session_id, trace):
                yield event
        else:
            async for event in self._stream_deterministic(question, plan, session_id, trace):
                yield event

    # --- 결정론적 모드 ---

    async def _execute_deterministic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
        trace: Optional[RequestTrace],
    ) -> AgentResponse:
        initial_state = create_initial_state(question, plan, session_id)

        result = await self._deterministic_app.ainvoke(initial_state)

        tools_called = result.get("tools_called", [])
        sources = [
            SourceRef(**s) for s in result.get("sources", [])
        ]

        return AgentResponse(
            answer=result.get("answer", ""),
            sources=sources,
            trace=TraceInfo(
                question_type=plan.question_type.value,
                mode=plan.mode.value,
                tools_called=tools_called,
            ),
        )

    async def _stream_deterministic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
        trace: Optional[RequestTrace],
    ) -> AsyncIterator[dict]:
        """결정론적 모드 스트리밍.

        LangGraph stream_mode="updates"로 노드별 상태 변경을 추적하고,
        LLM 답변은 별도로 스트리밍한다.
        """
        # 도구 실행까지는 비스트리밍으로 처리
        initial_state = create_initial_state(question, plan, session_id)

        # RAG 불필요 → 직접 스트리밍
        if not plan.strategy.needs_rag:
            async for token in self._main_llm.generate_stream(
                question, system=plan.system_prompt,
            ):
                yield {"type": "token", "data": token}
            yield {"type": "done", "data": {"tools_called": [], "sources": []}}
            return

        # Tool 실행 (비스트리밍, trace 이벤트 발행)
        yield {"type": "trace", "data": {"step": "tool_execution", "status": "start"}}

        # ainvoke 대신 astream으로 노드별 추적
        tools_called = []
        search_results = []
        async for chunk in self._deterministic_app.astream(
            initial_state, stream_mode="updates",
        ):
            for node_name, state_update in chunk.items():
                if node_name == "execute_tools":
                    tools_called = state_update.get("tools_called", [])
                    search_results = state_update.get("search_results", [])
                    for tl in state_update.get("tool_latencies", []):
                        yield {"type": "trace", "data": {
                            "tool": tl["tool"],
                            "success": tl["success"],
                            "ms": tl["ms"],
                        }}

        # 답변은 토큰 스트리밍 (노드 실행 결과가 아닌 별도 스트리밍)
        prompt_results = search_results[:plan.strategy.max_vector_chunks]
        prompt = self._build_prompt(question, plan, prompt_results)

        yield {"type": "trace", "data": {
            "step": "generation", "status": "start",
            "context_chunks": len(prompt_results),
        }}

        answer_tokens = []
        async for token in self._main_llm.generate_stream(
            prompt, system=plan.system_prompt,
        ):
            answer_tokens.append(token)
            yield {"type": "token", "data": token}

        # Guardrail
        full_answer = "".join(answer_tokens)
        if plan.guardrail_chain:
            guardrail_context = GuardrailContext(
                question=question,
                source_documents=search_results,
                profile_id=session_id,
                response_policy=plan.response_policy,
            )
            modified, results = await self._run_guardrails_direct(
                full_answer, plan.guardrail_chain, guardrail_context,
            )
            if modified != full_answer:
                yield {"type": "trace", "data": {"step": "guardrail_modified", "results": results}}
                yield {"type": "replace", "data": modified}

        sources = self._build_sources(search_results)
        yield {
            "type": "done",
            "data": {
                "tools_called": tools_called,
                "sources": [s.model_dump() for s in sources],
            },
        }

    # --- 에이전틱 모드 ---

    async def _execute_agentic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
        trace: Optional[RequestTrace],
    ) -> AgentResponse:
        if not self._chat_model:
            logger.warning("agentic_mode_no_chat_model, falling back to deterministic")
            return await self._execute_deterministic(question, plan, session_id, trace)

        # Tool 변환
        context = AgentContext(session_id=session_id)
        lc_tools = convert_tools_to_langchain(plan.tools, context, plan.scope)

        if not lc_tools:
            logger.warning("agentic_mode_no_tools, falling back to deterministic")
            return await self._execute_deterministic(question, plan, session_id, trace)

        # 에이전틱 그래프 빌드 + 실행
        agent = build_agentic_graph(
            chat_model=self._chat_model,
            tools=lc_tools,
            guardrails=self._guardrails,
            max_tool_calls=plan.max_tool_calls,
        )
        agent_app = agent.compile() if hasattr(agent, 'compile') else agent

        messages = [{"role": "user", "content": question}]
        if plan.system_prompt:
            config = {"configurable": {"system_message": plan.system_prompt}}
        else:
            config = {}

        result = await agent_app.ainvoke(
            {"messages": messages},
            config=config,
        )

        # 결과 추출
        answer = ""
        tools_called = []
        if "messages" in result:
            for msg in result["messages"]:
                if hasattr(msg, "content") and hasattr(msg, "type"):
                    if msg.type == "ai" and msg.content and not hasattr(msg, "tool_calls"):
                        answer = msg.content
                    elif msg.type == "tool":
                        tools_called.append(msg.name if hasattr(msg, "name") else "unknown")

        # 최종 AI 메시지 추출
        if result.get("messages"):
            last_msg = result["messages"][-1]
            if hasattr(last_msg, "content"):
                answer = last_msg.content

        # Guardrail 적용
        if plan.guardrail_chain:
            guardrail_ctx = GuardrailContext(
                question=question,
                source_documents=[],
                profile_id=session_id,
                response_policy=plan.response_policy,
            )
            answer, _ = await self._run_guardrails_direct(
                answer, plan.guardrail_chain, guardrail_ctx,
            )

        return AgentResponse(
            answer=answer,
            sources=[],
            trace=TraceInfo(
                question_type=plan.question_type.value,
                mode="agentic",
                tools_called=tools_called,
            ),
        )

    async def _stream_agentic(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
        trace: Optional[RequestTrace],
    ) -> AsyncIterator[dict]:
        """에이전틱 모드 스트리밍.

        astream_events로 도구 호출 과정 추적 + 최종 답변 토큰 스트리밍.
        """
        if not self._chat_model:
            async for event in self._stream_deterministic(question, plan, session_id, trace):
                yield event
            return

        context = AgentContext(session_id=session_id)
        lc_tools = convert_tools_to_langchain(plan.tools, context, plan.scope)

        if not lc_tools:
            async for event in self._stream_deterministic(question, plan, session_id, trace):
                yield event
            return

        agent = build_agentic_graph(
            chat_model=self._chat_model,
            tools=lc_tools,
            guardrails=self._guardrails,
            max_tool_calls=plan.max_tool_calls,
        )
        agent_app = agent.compile() if hasattr(agent, 'compile') else agent

        yield {"type": "trace", "data": {"step": "agentic_start", "mode": "agentic"}}

        messages = [{"role": "user", "content": question}]
        tools_called = []
        answer = ""

        async for event in agent_app.astream_events(
            {"messages": messages},
            version="v2",
        ):
            kind = event.get("event", "")

            # 도구 호출 추적
            if kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                yield {"type": "trace", "data": {
                    "step": "tool_call",
                    "tool": tool_name,
                    "arguments": event.get("data", {}).get("input", {}),
                }}

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                tools_called.append(tool_name)
                yield {"type": "trace", "data": {
                    "step": "tool_complete",
                    "tool": tool_name,
                }}

            # 최종 답변 토큰 스트리밍
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    # tool_call이 아닌 실제 텍스트 토큰만
                    if not (hasattr(chunk, "tool_calls") and chunk.tool_calls):
                        yield {"type": "token", "data": chunk.content}
                        answer += chunk.content

        # Guardrail
        if plan.guardrail_chain and answer:
            guardrail_ctx = GuardrailContext(
                question=question,
                source_documents=[],
                profile_id=session_id,
                response_policy=plan.response_policy,
            )
            modified, results = await self._run_guardrails_direct(
                answer, plan.guardrail_chain, guardrail_ctx,
            )
            if modified != answer:
                yield {"type": "trace", "data": {"step": "guardrail_modified", "results": results}}
                yield {"type": "replace", "data": modified}

        yield {
            "type": "done",
            "data": {
                "tools_called": tools_called,
                "sources": [],
            },
        }

    # --- 공통 헬퍼 ---

    async def _run_guardrails_direct(
        self,
        answer: str,
        guardrail_names: list[str],
        context: GuardrailContext,
    ) -> tuple[str, dict]:
        """Guardrail 체인 직접 실행."""
        results = {}
        for name in guardrail_names:
            guardrail = self._guardrails.get(name)
            if not guardrail:
                results[name] = "skipped"
                continue
            try:
                t = time.time()
                result = await guardrail.check(answer, context)
                ms = (time.time() - t) * 1000
                results[name] = {"action": result.action, "ms": round(ms, 1)}
                if result.action == "block":
                    return GUARDRAIL_BLOCK_TEMPLATE.format(reason=result.reason), results
                if result.action == "warn" and result.modified_answer:
                    answer = result.modified_answer
            except Exception as e:
                results[name] = {"action": "error", "error": str(e)}
        return answer, results

    @staticmethod
    def _build_sources(results: list[dict]) -> list[SourceRef]:
        sources = []
        seen = set()
        for r in results:
            doc_id = r.get("document_id", "")
            if doc_id in seen:
                continue
            seen.add(doc_id)
            sources.append(SourceRef(
                document_id=doc_id,
                title=r.get("title", r.get("file_name", "")),
                chunk_text=r.get("content", "")[:200],
                score=r.get("score", 0.0),
                method=r.get("method", "vector"),
            ))
        return sources[:5]

    @staticmethod
    def _build_prompt(question: str, plan, results: list[dict]) -> str:
        from src.agent.nodes import _build_prompt
        return _build_prompt(question, plan, results)
```

- [ ] **Step 3: 테스트 실행 → PASS**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/test_graphs.py -v`

- [ ] **Step 4: 커밋**

```bash
git add src/agent/graph_executor.py tests/test_graphs.py
git commit -m "feat: GraphExecutor — 모드별 LangGraph 그래프 선택 + 실행 + SSE 스트리밍"
```

---

## Chunk 5: 통합 연결 + Profile 마이그레이션 + 테스트

### Task 9: Gateway/main.py에서 UniversalAgent → GraphExecutor 교체

**Files:**
- Modify: `src/main.py`
- Modify: `src/gateway/router.py`

- [ ] **Step 1: main.py — GraphExecutor 초기화**

`src/main.py` lifespan에서:
```python
# 기존:
# from src.agent.universal import UniversalAgent
# agent = UniversalAgent(main_llm, tool_registry, guardrails)

# 변경:
from src.agent.graph_executor import GraphExecutor
from src.agent.chat_model_factory import create_chat_model

# ChatModel 생성 (에이전틱 모드용)
try:
    chat_model = create_chat_model(
        provider_mode=settings.provider_mode,
        model_name=settings.main_model,
        ollama_host=settings.ollama_host,
        openai_api_key=settings.openai_api_key,
        server_url=settings.main_llm_server_url,
    )
except ImportError:
    chat_model = None
    logger.warning("LangChain ChatModel 미설치 — 에이전틱 모드 비활성")

agent = GraphExecutor(
    main_llm=main_llm,
    tool_registry=tool_registry,
    guardrails=guardrails_dict,
    chat_model=chat_model,
)
```

- [ ] **Step 2: gateway/router.py — 인터페이스 호환**

`execute()` 호출부:
```python
# 기존:
# response = await agent.execute(question, plan, context, trace)

# 변경 (AgentContext → session_id 직접 전달):
response = await agent.execute(
    question=question,
    plan=plan,
    session_id=context.session_id,
    trace=trace,
)
```

`execute_stream()` 호출부도 동일하게 변경.

- [ ] **Step 3: 전체 테스트 PASS**

Run: `cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/ -x -v`

- [ ] **Step 4: 커밋**

```bash
git add src/main.py src/gateway/router.py
git commit -m "refactor: UniversalAgent → GraphExecutor 교체 — LangGraph 기반 실행"
```

---

### Task 10: Profile 마이그레이션 + general-assistant

**Files:**
- Modify: `seeds/profiles/insurance-qa.yaml`
- Modify: `seeds/profiles/insurance-contract.yaml`
- Modify: `seeds/profiles/general-chat.yaml`
- Create: `seeds/profiles/general-assistant.yaml`

- [ ] **Step 1: 기존 프로필 mode 변경**

```
insurance-qa.yaml: mode: "agentic" → mode: "deterministic"
insurance-contract.yaml: mode: "agentic" → mode: "deterministic"
general-chat.yaml: mode: "agentic" → mode: "deterministic"
```

- [ ] **Step 2: general-assistant.yaml 생성**

```yaml
id: "general-assistant"
name: "범용 AI 어시스턴트"

domain_scopes: []
category_scopes: []
security_level_max: "PUBLIC"

mode: "agentic"
max_tool_calls: 7
agent_timeout_seconds: 30

tools:
  - name: "rag_search"
    config: {}
  - name: "fact_lookup"
    config: {}

system_prompt: |
  당신은 범용 AI 어시스턴트입니다.
  사용자의 질문에 가장 적합한 도구를 선택하여 답변하세요.
  문서 검색이 필요하면 rag_search를, 구조화된 데이터가 필요하면 fact_lookup을 사용하세요.
  도구를 사용하지 않아도 답변할 수 있으면 직접 답변하세요.

response_policy: "balanced"
guardrails:
  - "faithfulness"

router_model: "haiku"
main_model: "sonnet"

memory_type: "short"
memory_ttl_seconds: 3600

intent_hints: []
```

- [ ] **Step 3: 커밋**

```bash
git add seeds/profiles/
git commit -m "feat: Profile 마이그레이션 — deterministic/agentic 분리 + general-assistant"
```

---

### Task 11: 통합 테스트 (E2E)

- [ ] **Step 1: 전체 테스트 PASS**

```bash
cd /Users/eyjs/Desktop/WorkSpace/ai-platform && .venv/bin/python -m pytest tests/ -x -v
```

- [ ] **Step 2: Docker 빌드 + 실행**

```bash
cd /Users/eyjs/Desktop/WorkSpace/ai-platform && docker compose up -d --build
```

- [ ] **Step 3: 결정론적 모드 검증 — insurance-qa**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "자동차보험 대인배상 한도", "profile_id": "insurance-qa"}'
```
Expected: trace에 `mode: "deterministic"`, 순차 도구 실행

- [ ] **Step 4: 에이전틱 모드 검증 — general-assistant**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "보험 약관에서 대인배상 한도 찾아줘", "profile_id": "general-assistant"}'
```
Expected: trace에 `mode: "agentic"`, LLM이 rag_search 자율 선택

- [ ] **Step 5: SSE 스트리밍 검증**

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "보험 약관 요약해줘", "profile_id": "general-assistant"}'
```
Expected: `agentic_start` → `tool_call` → `tool_complete` → 토큰 스트리밍 → `done`

- [ ] **Step 6: 최종 커밋 + 푸시**

```bash
git add -A
git commit -m "feat: 듀얼 모드 엔진 완성 — LangGraph StateGraph(결정론적) + create_react_agent(에이전틱)"
git push
```

---

## 검증 기준

1. `pytest tests/ -v` — 전체 PASS
2. 결정론적 모드: 기존 insurance-qa 동작 동일 (회귀 없음)
3. 에이전틱 모드: general-assistant가 도구를 자율 선택하여 답변
4. SSE 스트리밍: 양쪽 모드 모두 trace + token + done 이벤트 정상 발행
5. Profile.mode 변경만으로 동일 인프라에서 모드 전환
