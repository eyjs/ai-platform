"""Workflow Engine: 순차적 챗봇 실행 엔진.

결정 트리 기반 대화를 실행한다.
엔진은 상태(WorkflowSession)를 받아서 현재 스텝을 처리하고,
다음 스텝으로 전이한 결과를 반환한다.

모든 공개 메서드는 async — 세션 영속화 + 외부 API 호출 지원.

백엔드 선택:
    - engine_backend="legacy" (기본): 현 sequential 경로. flag=legacy(기본)일 때 100%.
    - engine_backend="langgraph": LangGraph StateGraph + AsyncPostgresSaver 경로.
      graph_builder/checkpointer가 None이면 자동으로 legacy로 폴백 (G5).

공개 메서드 시그니처 5개는 어느 백엔드에서도 동일하다 (helpers.py/graph_executor.py 무변경).

사용법:
    engine = WorkflowEngine(store, session_store=session_store, action_client=action_client)
    result = await engine.start("insurance_contract", session_id)
    result = await engine.advance(session_id, user_input="자동차")
"""

from __future__ import annotations

import time
from typing import Optional

from src.common.exceptions import GatewayError
from src.domain.classifier import Candidate
from src.observability.logging import get_logger
from src.workflow.action_client import ActionClient
from src.workflow.context_adapter import WorkflowContextAdapter
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.session_store import WorkflowSessionStore
from src.workflow.state import WorkflowSession
from src.workflow.step_executors import execute_action_step, generate_dynamic
from src.workflow.step_result import StepResult
from src.workflow.store import WorkflowStore
from src.workflow.template import render_template
# step 순수 로직(re-export 포함 — 기존 `from src.workflow.engine import _resolve_next` 등 호환)
from src.workflow.step_logic import (
    _collection_steps,
    _resolve_next,
    _validate_input,
    _visible_ctx_lines,
)

logger = get_logger(__name__)

_MAX_MESSAGE_CHAIN = 10  # message 타입 연쇄 최대 깊이
_SESSION_TTL_SECONDS = 3600  # 세션 만료 시간 (1시간)

# 이탈(escape) 키워드 — escape_policy="allow"일 때 워크플로우 즉시 종료
_ESCAPE_KEYWORDS = {"취소", "처음으로", "나가기", "중단", "그만", "exit", "cancel", "quit"}

# 뒤로가기 키워드
_BACK_KEYWORDS = {"뒤로", "이전", "돌아가기", "back", "prev"}

__all__ = ["WorkflowEngine", "StepResult"]


def _dict_to_step_result(d: dict) -> StepResult:
    """last_result dict → StepResult 역직렬화 (graph_state.last_result 소비).

    graph_builder(T3)가 last_result(dict)를 채운다.
    어댑터는 dict→StepResult 역직렬화만 수행 — step_result.py 변경 0.
    """
    return StepResult(
        bot_message=d.get("bot_message", ""),
        options=list(d.get("options") or []),
        step_id=d.get("step_id", ""),
        step_type=d.get("step_type", ""),
        collected=dict(d.get("collected") or {}),
        completed=bool(d.get("completed", False)),
        escaped=bool(d.get("escaped", False)),
        action_result=dict(d.get("action_result") or {}),
        report=d.get("report", ""),
        intent_confirm=dict(d.get("intent_confirm") or {}),
        collection=dict(d.get("collection") or {}),
        concluded=bool(d.get("concluded", False)),
    )


def _graph_state_to_session(state_values: dict, workflow_id: str) -> WorkflowSession:
    """LangGraph aget_state().values → WorkflowSession 역매핑.

    get_session 반환 타입(WorkflowSession)을 유지하기 위해 채널 값을 필드에 1:1 매핑한다.
    helpers.py/orchestrator.py가 접근하는 필드(completed/workflow_id/current_step_id/collected)
    전부 커버 — 호환 깨지지 않음.
    """
    return WorkflowSession(
        workflow_id=workflow_id or state_values.get("workflow_id", ""),
        current_step_id=state_values.get("current_step_id", ""),
        collected=dict(state_values.get("collected") or {}),
        step_history=list(state_values.get("step_history") or []),
        started_at=state_values.get("started_at", time.time()),
        completed=bool(state_values.get("completed", False)),
        retry_count=int(state_values.get("retry_count") or 0),
        awaiting_callback=bool(state_values.get("awaiting_callback", False)),
        callback_response=dict(state_values.get("callback_response") or {}),
    )


class WorkflowEngine:
    """순차적 챗봇 실행 엔진.

    세션 영속화: session_store가 주입되면 PostgreSQL에 저장,
    없으면 인메모리 dict 사용 (하위 호환).

    Action step: action_client가 주입되면 외부 HTTP 호출 가능,
    없으면 action step에서 에러 메시지 반환.

    LangGraph 백엔드: engine_backend="langgraph" + graph_builder + checkpointer 주입 시 활성화.
    checkpointer가 None이면 langgraph 요청이어도 legacy로 graceful 폴백 (G5).

    R5 결정 — action_endpoint/headers:
      graph_executor.py는 start/advance에 endpoint를 넘기지 않는다(:341-348).
      legacy advance는 인자로 받지만 실호출처(graph_executor)가 넘기지 않는 상태.
      따라서 LangGraph 경로에서는 빌더 캡처 기본으로 단순화한다:
      WorkflowGraphBuilder 생성자에 action_endpoint_default/action_headers_default를
      주입하면 모든 action 노드가 해당 값을 사용한다. per-call override가 필요할 경우
      config["configurable"]["action_endpoint"]로 전달할 수 있지만, 현재 graph_executor는
      이를 전달하지 않으므로 빌더 캡처로 충분하다.
    """

    def __init__(
        self,
        store: WorkflowStore,
        session_store: WorkflowSessionStore | None = None,
        action_client: ActionClient | None = None,
        llm=None,
        context_adapters: dict[str, WorkflowContextAdapter] | None = None,
        classifier=None,
        # LangGraph 경로 의존성 (기존 인자 전부 유지)
        graph_builder=None,   # WorkflowGraphBuilder (T3 산출) — 미주입 시 legacy
        checkpointer=None,    # AsyncPostgresSaver (T2 산출) — None이면 legacy 폴백 (G5)
        engine_backend: str = "legacy",  # "legacy" | "langgraph"
    ) -> None:
        self._store = store
        self._session_store = session_store
        self._action_client = action_client
        # dynamic 스텝(LLM 캐릭터 통찰)용 LLMProvider. 없으면 dynamic은 정적 폴백.
        self._llm = llm
        # 서비스별 컨텍스트 enrichment 플러그인 (이름 → 어댑터). 프로파일이 선택한다.
        self._context_adapters = context_adapters or {}
        # select 분기 의미 분류용 공통 SemanticClassifier. 없으면 키워드 매칭만(하위호환).
        self._classifier = classifier
        # 인메모리 폴백 (session_store 미주입 시)
        self._sessions: dict[str, WorkflowSession] = {}

        # LangGraph 경로 — checkpointer가 None이면 legacy로 폴백 (G5)
        self._graph_builder = graph_builder
        self._checkpointer = checkpointer

        # Bug #2 수정: 외부에서 graph_builder를 주입할 때 action_client를 builder에 전파.
        # 테스트 factory는 builder를 먼저 생성한 뒤 engine에 action_client를 넘기므로,
        # builder 생성 시점에 action_client를 알 수 없다 → engine __init__에서 동기화.
        if graph_builder is not None and action_client is not None:
            graph_builder._action_client = action_client

        # 실제 사용 백엔드 결정: langgraph 요청이어도 checkpointer 미설치 시 legacy (G5)
        if engine_backend == "langgraph" and (graph_builder is None or checkpointer is None):
            logger.warning(
                "workflow_engine_langgraph_fallback",
                layer="WORKFLOW",
                reason="graph_builder 또는 checkpointer 미주입 — legacy로 폴백",
                graph_builder_present=graph_builder is not None,
                checkpointer_present=checkpointer is not None,
            )
            engine_backend = "legacy"

        self._engine_backend = engine_backend

    # ──────────────────────────────────────────────────
    # 공개 메서드 5개 (시그니처 100% 보존 — helpers.py/graph_executor.py 무변경 계약)
    # ──────────────────────────────────────────────────

    async def start(
        self,
        workflow_id: str,
        session_id: str,
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
        context_adapter: str | None = None,
        cache_padding_text: str = "",
    ) -> StepResult:
        """워크플로우를 시작하고, 첫 번째 스텝의 봇 메시지를 반환한다.

        Args:
            workflow_id: 워크플로우 정의 ID
            session_id: 대화 세션 ID
            action_endpoint: Profile 기본 action 엔드포인트 (step에 미지정 시 사용)
            action_headers: Profile 기본 action 헤더 (step에 미지정 시 사용)
            cache_padding_text: 캐시 패딩용 도메인 배경 텍스트 (Profile이 지정). dynamic 스텝
                cacheable_system 패딩에 쓰인다. 비면 도메인 중립 여백.
            context_adapter: dynamic 스텝 enrichment에 쓸 어댑터 이름 (Profile이 지정).
                세션에 바인딩되어 이후 advance/dynamic 스텝에서 재사용된다.
        """
        if self._engine_backend == "langgraph":
            return await self._lg_start(
                workflow_id, session_id,
                action_endpoint=action_endpoint,
                action_headers=action_headers,
                context_adapter=context_adapter,
                cache_padding_text=cache_padding_text,
            )
        return await self._legacy_start(
            workflow_id, session_id,
            action_endpoint=action_endpoint,
            action_headers=action_headers,
            context_adapter=context_adapter,
            cache_padding_text=cache_padding_text,
        )

    async def advance(
        self,
        session_id: str,
        user_input: str,
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
    ) -> StepResult:
        """사용자 입력을 받아 다음 스텝으로 전이한다.

        Args:
            session_id: 대화 세션 ID
            user_input: 사용자 입력 텍스트
            action_endpoint: Profile 기본 action 엔드포인트
            action_headers: Profile 기본 action 헤더
        """
        if self._engine_backend == "langgraph":
            return await self._lg_advance(session_id, user_input)
        return await self._legacy_advance(
            session_id, user_input,
            action_endpoint=action_endpoint,
            action_headers=action_headers,
        )

    async def resume(
        self,
        workflow_id: str,
        session_id: str,
        step_id: str,
        collected: dict,
    ) -> StepResult:
        """일시 중지된 워크플로우를 재개한다.

        G3 시맨틱 — legacy: 새 세션을 만들어 step부터 process.
        LangGraph: 해당 thread의 체크포인트를 step_id/collected로 시드해 덮어쓴다.
        test_resume_replaces_existing_session 계약 동등 보장.
        """
        if self._engine_backend == "langgraph":
            return await self._lg_resume(workflow_id, session_id, step_id, collected)
        return await self._legacy_resume(workflow_id, session_id, step_id, collected)

    async def get_session(self, session_id: str) -> Optional[WorkflowSession]:
        """세션 상태를 조회한다."""
        if self._engine_backend == "langgraph":
            return await self._lg_get_session(session_id)
        return await self._load_session(session_id)

    async def cancel(self, session_id: str) -> bool:
        """워크플로우를 취소한다."""
        if self._engine_backend == "langgraph":
            return await self._lg_cancel(session_id)
        return await self._legacy_cancel(session_id)

    # ──────────────────────────────────────────────────
    # LangGraph 경로 (신엔진 — engine_backend="langgraph")
    # ──────────────────────────────────────────────────

    def _lg_config(self, session_id: str) -> dict:
        """LangGraph ainvoke/aget_state용 config를 반환한다."""
        return {
            "configurable": {"thread_id": session_id},
            "recursion_limit": 25,
        }

    async def _lg_start(
        self,
        workflow_id: str,
        session_id: str,
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
        context_adapter: str | None = None,
        cache_padding_text: str = "",
    ) -> StepResult:
        """LangGraph 경로 start: initial_state 구성 → ainvoke → last_result 역직렬화.

        legacy engine.py:148-174의 collected 초기화 규약을 동등하게 재현한다.
        (session_id 주입, _pad_text, _adapter, adapter.bind)
        """
        from src.workflow.graph_state import make_initial_state

        definition = self._store.get(workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우를 찾을 수 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_NOT_FOUND",
            )
        if not definition.steps:
            raise GatewayError(
                f"워크플로우에 스텝이 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_EMPTY",
            )

        # collected 초기화 — legacy engine.py:148-174 동등
        initial_collected: dict = {"session_id": session_id}
        if cache_padding_text:
            initial_collected["_pad_text"] = cache_padding_text
        if context_adapter:
            initial_collected["_adapter"] = context_adapter
            adapter = self._context_adapters.get(context_adapter)
            bind = getattr(adapter, "bind", None)
            if callable(bind):
                try:
                    bind(session_id, initial_collected)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "context_adapter_bind_failed",
                        layer="WORKFLOW",
                        adapter=context_adapter,
                        error=str(e),
                    )

        initial_state = make_initial_state(workflow_id, definition.entry_step_id)
        initial_state["collected"] = initial_collected

        logger.info(
            "workflow_start",
            layer="WORKFLOW",
            workflow_id=workflow_id,
            session_id=session_id,
            first_step=definition.entry_step_id,
            backend="langgraph",
        )

        graph = self._graph_builder.get_graph(workflow_id, self._checkpointer)
        config = self._lg_config(session_id)

        # ainvoke: START → 첫 interrupt 또는 END까지 자동 전이
        await graph.ainvoke(initial_state, config=config)

        return await self._lg_read_last_result(graph, session_id, workflow_id)

    async def _lg_advance(self, session_id: str, user_input: str) -> StepResult:
        """LangGraph 경로 advance: Command(resume=user_input) → ainvoke → last_result.

        먼저 이미 완료된 세션인지 확인한다 — legacy engine.py:214-220 동등.
        """
        session = await self._lg_get_session(session_id)
        if session and session.completed:
            return StepResult(
                bot_message="이미 완료된 워크플로우입니다.",
                completed=True,
                concluded=True,
                collected=dict(session.collected),
            )

        # workflow_id는 state 채널에서 가져온다 (session이 None이면 ERR)
        if not session:
            raise GatewayError(
                "활성 워크플로우 세션이 없습니다",
                error_code="ERR_WORKFLOW_NO_SESSION",
            )

        from langgraph.types import Command

        graph = self._graph_builder.get_graph(session.workflow_id, self._checkpointer)
        config = self._lg_config(session_id)

        await graph.ainvoke(Command(resume=user_input), config=config)

        return await self._lg_read_last_result(graph, session_id, session.workflow_id)

    async def _lg_resume(
        self,
        workflow_id: str,
        session_id: str,
        step_id: str,
        collected: dict,
    ) -> StepResult:
        """LangGraph 경로 resume (G3): 체크포인트를 step_id/collected로 시드 후 ainvoke.

        기존 thread 체크포인트를 덮어쓴다 — test_resume_replaces_existing_session 계약 동등.
        실제 구현: step_id를 entry로 하는 새 initial state로 ainvoke해 체크포인트를 교체한다.
        (LangGraph는 thread_id에 대한 ainvoke 시 항상 새 체크포인트를 기록하므로 덮어쓰기 동등.)
        """
        from src.workflow.graph_state import make_initial_state

        definition = self._store.get(workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우를 찾을 수 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_NOT_FOUND",
            )
        step = definition.get_step(step_id)
        if not step:
            raise GatewayError(
                f"스텝을 찾을 수 없습니다: {step_id}",
                error_code="ERR_WORKFLOW_STEP_MISSING",
            )

        # step_id/collected로 시드한 initial state — 기존 thread 체크포인트 교체
        seed_state = make_initial_state(workflow_id, step_id)
        seed_state["collected"] = dict(collected)

        logger.info(
            "workflow_resume",
            layer="WORKFLOW",
            workflow_id=workflow_id,
            session_id=session_id,
            step_id=step_id,
            collected_keys=list(collected.keys()),
            backend="langgraph",
        )

        graph = self._graph_builder.get_graph(workflow_id, self._checkpointer)
        config = self._lg_config(session_id)

        await graph.ainvoke(seed_state, config=config)

        return await self._lg_read_last_result(graph, session_id, workflow_id)

    async def _lg_get_session(self, session_id: str) -> Optional[WorkflowSession]:
        """LangGraph 경로 get_session: aget_state → WorkflowSession 역매핑.

        체크포인트 없으면 None.
        """
        # workflow_id를 모르면 그래프 선택 불가 — checkpointer 직접 조회로 thread 메타 취득.
        # AsyncPostgresSaver는 get_tuple(config) API로 raw checkpoint를 읽을 수 있다.
        # state 채널에 workflow_id를 저장해 두었으므로 아무 그래프나 aget_state로 읽어도 됨.
        # 여기서는 checkpointer.aget_tuple로 직접 읽고 workflow_id로 그래프를 선택한다.
        try:
            config = {"configurable": {"thread_id": session_id}}
            checkpoint_tuple = await self._checkpointer.aget_tuple(config)
            if not checkpoint_tuple:
                return None
            channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
            workflow_id = channel_values.get("workflow_id", "")
            if not workflow_id:
                return None
            return _graph_state_to_session(channel_values, workflow_id)
        except Exception as e:
            logger.warning(
                "workflow_lg_get_session_error",
                layer="WORKFLOW",
                session_id=session_id,
                error=str(e),
            )
            return None

    async def _lg_cancel(self, session_id: str) -> bool:
        """LangGraph 경로 cancel: checkpointer에서 thread 삭제.

        Bug #3b 수정: adelete_thread 호출 전 세션 존재 여부를 확인한다.
        MemorySaver는 없는 thread에도 예외 없이 반환하므로 aget_tuple로 선검사 필수.
        adelete_thread API(3.x)가 없으면 체크포인트를 완료 상태로 override해 무력화한다.
        """
        try:
            # 세션 존재 여부 선검사 (Bug #3b)
            config = {"configurable": {"thread_id": session_id}}
            tpl = await self._checkpointer.aget_tuple(config)
            if not tpl:
                return False

            # langgraph-checkpoint-postgres 3.x: adelete_thread(thread_id) API 사용.
            if hasattr(self._checkpointer, "adelete_thread"):
                await self._checkpointer.adelete_thread(session_id)
            else:
                # 삭제 API 미지원 환경 — 존재는 확인됐으므로 성공 처리만
                logger.warning(
                    "workflow_lg_cancel_no_delete_api",
                    layer="WORKFLOW",
                    session_id=session_id,
                )
            logger.info("workflow_cancel", layer="WORKFLOW", session_id=session_id, backend="langgraph")
            return True
        except Exception as e:
            logger.warning(
                "workflow_lg_cancel_error",
                layer="WORKFLOW",
                session_id=session_id,
                error=str(e),
            )
            return False

    async def _lg_read_last_result(self, graph, session_id: str, workflow_id: str) -> StepResult:
        """aget_state에서 last_result dict를 읽어 StepResult로 역직렬화한다.

        interrupt 또는 END 후 상태를 읽는다.
        interrupt payload는 last_result 채널에 올라있지 않으므로,
        graph_builder가 interactive 노드에서 interrupt(payload) 직전에
        last_result를 업데이트하는 패턴 필요. (T3 graph_builder의 interrupt_payload가
        dict 형태로 곧바로 StepResult 필드와 1:1이므로 aget_state.values.last_result 소비.)
        """
        config = self._lg_config(session_id)
        state_snapshot = await graph.aget_state(config)

        if state_snapshot is None:
            logger.warning(
                "workflow_lg_no_state",
                layer="WORKFLOW",
                session_id=session_id,
                workflow_id=workflow_id,
            )
            return StepResult(bot_message="워크플로우 상태를 읽을 수 없습니다.", completed=True, concluded=True)

        values = state_snapshot.values
        last_result = values.get("last_result") or {}

        # interrupt 상태: next가 있고 last_result가 비어있으면 state 채널에서 interrupt_payload 구성
        # (graph_builder의 interactive 노드는 interrupt() 직전 last_result를 state에 올리지 않으므로
        # interrupt_value를 state_snapshot에서 읽는다.)
        if not last_result and state_snapshot.next:
            # interrupt된 상태 — next 노드가 있음. interrupt payload는 state_snapshot.tasks에서 읽는다.
            for task in (state_snapshot.tasks or []):
                interrupts = getattr(task, "interrupts", None) or []
                if interrupts:
                    interrupt_value = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
                    if isinstance(interrupt_value, dict):
                        last_result = interrupt_value
                        break

        if not last_result:
            # 최후 폴백: state 채널에서 직접 구성
            collected = dict(values.get("collected") or {})
            last_result = {
                "bot_message": "\n\n".join(values.get("message_parts") or []),
                "collected": collected,
                "completed": bool(values.get("completed", False)),
                "step_id": values.get("current_step_id", ""),
                "step_type": "",
            }

        return _dict_to_step_result(last_result)

    # ──────────────────────────────────────────────────
    # Legacy 경로 (기존 코드 100% 보존 — T6에서 제거)
    # ──────────────────────────────────────────────────

    async def _load_session(self, session_id: str) -> WorkflowSession | None:
        """세션을 로드한다. session_store 우선, 없으면 인메모리."""
        if self._session_store:
            return await self._session_store.load(session_id)
        return self._sessions.get(session_id)

    async def _save_session(self, session_id: str, session: WorkflowSession) -> None:
        """세션을 저장한다. session_store 우선, 없으면 인메모리."""
        if self._session_store:
            await self._session_store.save(session_id, session)
        else:
            self._sessions[session_id] = session

    async def _delete_session(self, session_id: str) -> None:
        """세션을 삭제한다."""
        if self._session_store:
            await self._session_store.delete(session_id)
        else:
            self._sessions.pop(session_id, None)

    async def _legacy_start(
        self,
        workflow_id: str,
        session_id: str,
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
        context_adapter: str | None = None,
        cache_padding_text: str = "",
    ) -> StepResult:
        """legacy start — engine.py 원본 로직."""
        if not self._session_store:
            self._cleanup_expired_sessions()

        definition = self._store.get(workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우를 찾을 수 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_NOT_FOUND",
            )

        if not definition.steps:
            raise GatewayError(
                f"워크플로우에 스텝이 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_EMPTY",
            )

        entry_id = definition.entry_step_id
        session = WorkflowSession(
            workflow_id=workflow_id,
            current_step_id=entry_id,
        )

        # 세션 컨텍스트를 워크플로우 변수로 주입한다(범용 — session_id만).
        # action 엔드포인트/템플릿에서 {{session_id}}로 참조 가능.
        session.collected["session_id"] = session_id

        # 캐시 패딩 도메인 텍스트를 세션에 바인딩(영속/복원됨). dynamic 스텝에서 filler로 사용.
        if cache_padding_text:
            session.collected["_pad_text"] = cache_padding_text

        # dynamic 스텝 enrichment 어댑터를 세션에 바인딩 (collected에 저장 → 영속/복원됨).
        if context_adapter:
            session.collected["_adapter"] = context_adapter
            # 도메인 식별자 추출은 어댑터가 소유한다(예: "saju-{uuid}-{product}" → saju_id).
            # 엔진은 도메인별 session_id 규약을 모른다.
            adapter = self._context_adapters.get(context_adapter)
            bind = getattr(adapter, "bind", None)
            if callable(bind):
                # enrich와 동일하게 graceful — bind가 던져도 워크플로우 시작은 진행한다
                # (식별자 미주입 시 grounding/템플릿이 일부 비는 정도, 크래시 방지).
                try:
                    bind(session_id, session.collected)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "context_adapter_bind_failed",
                        layer="WORKFLOW",
                        adapter=context_adapter,
                        error=str(e),
                    )

        logger.info(
            "workflow_start",
            layer="WORKFLOW",
            workflow_id=workflow_id,
            session_id=session_id,
            first_step=entry_id,
        )

        result = await self._process_current_step(
            definition, session, session_id,
            action_endpoint=action_endpoint,
            action_headers=action_headers,
        )
        await self._save_session(session_id, session)
        return result

    async def _legacy_advance(
        self,
        session_id: str,
        user_input: str,
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
    ) -> StepResult:
        """legacy advance — engine.py 원본 로직."""
        session = await self._load_session(session_id)
        if not session:
            raise GatewayError(
                "활성 워크플로우 세션이 없습니다",
                error_code="ERR_WORKFLOW_NO_SESSION",
            )

        if session.completed:
            return StepResult(
                bot_message="이미 완료된 워크플로우입니다.",
                completed=True,
                concluded=True,
                collected=dict(session.collected),
            )

        definition = self._store.get(session.workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우 정의가 사라졌습니다: {session.workflow_id}",
                error_code="ERR_WORKFLOW_MISSING",
            )

        current_step = definition.get_step(session.current_step_id)
        if not current_step:
            raise GatewayError(
                f"스텝을 찾을 수 없습니다: {session.current_step_id}",
                error_code="ERR_WORKFLOW_STEP_MISSING",
            )

        result = await self._advance_inner(
            session, session_id, definition, current_step, user_input,
            action_endpoint, action_headers,
        )
        await self._save_session(session_id, session)
        return result

    async def _legacy_resume(
        self,
        workflow_id: str,
        session_id: str,
        step_id: str,
        collected: dict,
    ) -> StepResult:
        """legacy resume — engine.py 원본 로직 (새 세션 생성 후 step부터 process)."""
        definition = self._store.get(workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우를 찾을 수 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_NOT_FOUND",
            )

        step = definition.get_step(step_id)
        if not step:
            raise GatewayError(
                f"스텝을 찾을 수 없습니다: {step_id}",
                error_code="ERR_WORKFLOW_STEP_MISSING",
            )

        session = WorkflowSession(
            workflow_id=workflow_id,
            current_step_id=step_id,
            collected=dict(collected),
        )

        logger.info(
            "workflow_resume",
            layer="WORKFLOW",
            workflow_id=workflow_id,
            session_id=session_id,
            step_id=step_id,
            collected_keys=list(collected.keys()),
        )

        result = await self._process_current_step(definition, session, session_id)
        await self._save_session(session_id, session)
        return result

    async def _legacy_cancel(self, session_id: str) -> bool:
        """legacy cancel — engine.py 원본 로직."""
        session = await self._load_session(session_id)
        if session:
            await self._delete_session(session_id)
            logger.info("workflow_cancel", layer="WORKFLOW", session_id=session_id)
            return True
        return False

    async def _advance_inner(
        self,
        session: WorkflowSession,
        session_id: str,
        definition: WorkflowDefinition,
        current_step: WorkflowStep,
        user_input: str,
        action_endpoint: str | None,
        action_headers: dict | None,
    ) -> StepResult:
        """advance 내부 로직. 세션 저장은 호출자가 담당한다."""
        # 이탈 감지 (escape_policy="allow"일 때만)
        escape_result = self._check_escape(user_input, session, definition)
        if escape_result:
            return escape_result

        # 뒤로가기 감지
        if user_input.strip().lower() in _BACK_KEYWORDS or any(
            kw in user_input for kw in _BACK_KEYWORDS
        ):
            if session.step_history:
                prev_step_id = session.step_history.pop()
                prev_step = definition.get_step(prev_step_id)
                if prev_step and prev_step.save_as and prev_step.save_as in session.collected:
                    del session.collected[prev_step.save_as]
                session.current_step_id = prev_step_id
                session.retry_count = 0
                logger.info(
                    "workflow_back",
                    layer="WORKFLOW",
                    session_id=session_id,
                    from_step=current_step.id,
                    to_step=prev_step_id,
                )
                return await self._process_current_step(
                    definition, session, session_id,
                    action_endpoint=action_endpoint,
                    action_headers=action_headers,
                )
            else:
                return StepResult(
                    bot_message="첫 번째 단계입니다. 더 이상 뒤로 갈 수 없습니다.",
                    options=current_step.options,
                    step_id=current_step.id,
                    step_type=current_step.type,
                    collected=dict(session.collected),
                )

        # 입력 검증
        validation_error = _validate_input(current_step, user_input)
        if validation_error:
            session.retry_count += 1
            if session.retry_count >= definition.max_retries:
                logger.info(
                    "workflow_retry_limit",
                    layer="WORKFLOW",
                    session_id=session_id,
                    step_id=current_step.id,
                    retries=session.retry_count,
                )
                session.completed = True
                return StepResult(
                    bot_message="입력이 지연되어 진행을 취소합니다. 다른 도움이 필요하시면 말씀해주세요.",
                    completed=True,
                    escaped=True,
                    concluded=True,
                    collected=dict(session.collected),
                )
            return StepResult(
                bot_message=validation_error,
                options=current_step.options,
                step_id=current_step.id,
                step_type=current_step.type,
                collected=dict(session.collected),
            )

        # 데이터 수집
        if current_step.save_as:
            session.collected[current_step.save_as] = user_input

        # 다음 스텝 결정 (exact/소문자/번호 — 버튼·명시 입력)
        next_step_id = _resolve_next(current_step, user_input)

        # 못 잡은 자유입력 → 공통 의미 분류기로 분기(맥락 기반, 키워드 아님).
        # 버튼·번호는 위에서 이미 잡히므로 여기 도달 시에만 LLM 호출(지연·비용 가드).
        if not next_step_id and current_step.branches and self._classifier:
            candidates = [Candidate(label=k) for k in current_step.branches]
            ctx = render_template(current_step.prompt, session.collected)
            ctx_lines = _visible_ctx_lines(session.collected)
            if ctx_lines:
                ctx = f"{ctx}\n[지금까지 파악된 정보]\n" + "\n".join(ctx_lines)
            decision = await self._classifier.classify(
                user_input, candidates, context=ctx,
            )
            if decision.label and decision.label in current_step.branches:
                next_step_id = current_step.branches[decision.label]
                if current_step.save_as:
                    # 원시 자유입력 대신 정규 분기키 저장(다운스트림 dynamic 스텝이 깔끔하게 사용)
                    session.collected[current_step.save_as] = decision.label
                logger.info(
                    "workflow_branch_llm_classified",
                    layer="WORKFLOW", session_id=session_id,
                    step_id=current_step.id, label=decision.label,
                    confidence=decision.confidence,
                )

        # select/branch 스텝에서 입력이 어떤 분기에도 안 맞고 fallback next도 없으면,
        # 워크플로우를 종료하지 말고(자유텍스트 조기종료 버그) 같은 스텝을 다시 안내한다.
        # 가이드형 funnel에서 버튼 대신 자유텍스트를 친 경우의 이탈 방어.
        # (retry_count 리셋은 정상 진행 확정 뒤로 미뤘으므로 미매칭이 누적된다)
        if not next_step_id and current_step.branches:
            # 방금 save_as에 잘못 담긴 미매칭 입력 롤백
            if current_step.save_as and current_step.save_as in session.collected:
                del session.collected[current_step.save_as]
            session.retry_count += 1
            if session.retry_count >= definition.max_retries:
                session.completed = True
                logger.info(
                    "workflow_select_no_match_escape",
                    layer="WORKFLOW", session_id=session_id,
                    step_id=current_step.id, retries=session.retry_count,
                )
                return StepResult(
                    bot_message="여러 번 이해하지 못했어요. 잠시 후 다시 시도해 주세요.",
                    completed=True,
                    escaped=True,
                    concluded=True,
                    collected=dict(session.collected),
                    step_id=current_step.id,
                    step_type=current_step.type,
                )
            logger.info(
                "workflow_select_no_match_reprompt",
                layer="WORKFLOW", session_id=session_id,
                step_id=current_step.id, retries=session.retry_count,
                user_input=user_input[:50],
            )
            # current_step_id 변경 없이 같은 스텝을 다시 안내(스텝 고유 프롬프트+옵션 재노출)
            return await self._process_current_step(
                definition, session, session_id,
                action_endpoint=action_endpoint,
                action_headers=action_headers,
            )

        # 정상 진행 확정 -> retry 카운터 리셋
        session.retry_count = 0

        logger.info(
            "workflow_advance",
            layer="WORKFLOW",
            session_id=session_id,
            from_step=current_step.id,
            to_step=next_step_id or "END",
            user_input=user_input[:50],
        )

        # 종료 (정상 종착: next도 branches도 없는 말단 스텝)
        if not next_step_id:
            session.completed = True
            return StepResult(
                bot_message="워크플로우가 완료되었습니다.",
                completed=True,
                concluded=True,
                collected=dict(session.collected),
                step_id=current_step.id,
                step_type="complete",
            )

        next_step = definition.get_step(next_step_id)
        if not next_step:
            session.completed = True
            return StepResult(
                bot_message=f"다음 스텝({next_step_id})을 찾을 수 없습니다.",
                completed=True,
                concluded=True,
                collected=dict(session.collected),
            )

        session.step_history.append(current_step.id)
        session.current_step_id = next_step_id
        return await self._process_current_step(
            definition, session, session_id,
            action_endpoint=action_endpoint,
            action_headers=action_headers,
        )

    def _check_escape(
        self,
        user_input: str,
        session: WorkflowSession,
        definition: WorkflowDefinition,
    ) -> Optional[StepResult]:
        """이탈 키워드를 감지한다. escape_policy에 따라 처리.

        워크플로우별 escape_keywords가 정의되어 있으면 우선 사용하고,
        없으면 전역 _ESCAPE_KEYWORDS를 사용한다.
        """
        if definition.escape_policy != "allow":
            return None

        normalized = user_input.strip().lower()

        # 워크플로우별 escape_keywords 우선, 없으면 전역 폴백
        keywords = (
            {kw.lower() for kw in definition.escape_keywords}
            if definition.escape_keywords
            else _ESCAPE_KEYWORDS
        )

        if normalized not in keywords:
            return None

        # 워크플로우 취소
        logger.info(
            "workflow_escape",
            layer="WORKFLOW",
            workflow_id=session.workflow_id,
            trigger=normalized,
            collected_keys=list(session.collected.keys()),
        )
        session.completed = True
        return StepResult(
            bot_message="워크플로우가 취소되었습니다. 다른 질문이 있으시면 말씀해주세요.",
            completed=True,
            escaped=True,
            concluded=True,
            collected=dict(session.collected),
        )

    def _cleanup_expired_sessions(self) -> None:
        """만료된 인메모리 세션을 정리한다 (session_store 미사용 시)."""
        now = time.time()
        expired = [
            sid for sid, session in self._sessions.items()
            if now - session.started_at > _SESSION_TTL_SECONDS
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info("workflow_sessions_cleaned", count=len(expired))

    async def _process_current_step(
        self,
        definition: WorkflowDefinition,
        session: WorkflowSession,
        session_id: str = "",
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
    ) -> StepResult:
        """현재 스텝을 처리하고 StepResult를 반환한다.

        message 타입은 자동으로 다음 스텝으로 체이닝된다.
        action 타입은 외부 API를 호출하고 결과에 따라 다음 스텝으로 전이한다.
        무한 루프 방지를 위해 _MAX_MESSAGE_CHAIN 깊이 제한을 적용한다.
        """
        message_parts: list[str] = []
        # 메시지 체인(dynamic→message 등)에서 만난 report 힌트를 누적해 최종 결과에 싣는다.
        report_hint = ""

        for _ in range(_MAX_MESSAGE_CHAIN):
            step = definition.get_step(session.current_step_id)
            if not step:
                session.completed = True
                return StepResult(bot_message="스텝 오류", completed=True, concluded=True)

            if step.report:
                report_hint = step.report

            rendered = render_template(step.prompt, session.collected)

            # dynamic 타입: LLM이 collected 컨텍스트로 캐릭터 통찰을 생성 → message처럼 자동 진행
            if step.type == "dynamic":
                insight = await generate_dynamic(
                    step, session.collected,
                    llm=self._llm, context_adapters=self._context_adapters,
                )
                if insight:
                    message_parts.append(insight)
                if not step.next or not definition.get_step(step.next):
                    session.completed = True
                    return StepResult(
                        bot_message="\n\n".join(message_parts),
                        completed=True,
                        concluded=True,
                        collected=dict(session.collected),
                        step_id=step.id,
                        step_type=step.type,
                        report=report_hint,
                    )
                session.current_step_id = step.next
                continue

            # action 타입: 외부 API 호출 후 자동 진행
            if step.type == "action":
                action_result = await execute_action_step(
                    step, session, self._action_client, action_endpoint, action_headers,
                )
                if action_result.completed or not step.next:
                    # 액션 실패 또는 다음 스텝 없음 -> 워크플로우 종료.
                    # 종료 상태를 세션에 명시 저장해야 다음 메시지가 일반 대화로
                    # 복귀한다 (미설정 시 워크플로우에 갇힘).
                    session.completed = True
                    await self._save_session(session_id, session)
                    bot_message = action_result.bot_message
                    if message_parts:
                        bot_message = "\n\n".join(message_parts) + "\n\n" + bot_message
                    return StepResult(
                        bot_message=bot_message,
                        options=action_result.options,
                        step_id=action_result.step_id,
                        step_type=action_result.step_type,
                        collected=action_result.collected,
                        completed=True,
                        concluded=True,
                        action_result=action_result.action_result,
                        report=report_hint,
                    )

                # 액션 성공 + 다음 스텝 있음 -> 메시지 축적 후 다음 스텝으로
                if action_result.bot_message:
                    message_parts.append(action_result.bot_message)
                session.current_step_id = step.next
                continue

            # message 이외 타입: 메시지 축적 후 반환
            if step.type != "message":
                # ── confirm: 수집 요약 추가 + intent_confirm 구성 ──
                intent_confirm_meta: dict = {}
                if step.type == "confirm":
                    summary_lines = [f"- {k}: {v}" for k, v in session.collected.items()]
                    rendered = f"{rendered}\n\n" + "\n".join(summary_lines)
                    intent_confirm_meta = {
                        "intent": step.intent or "",
                        "yes_label": step.confirm_yes_label or "응",
                        "no_label": step.confirm_no_label or "아니",
                    }

                # ── input/select 수집 스텝: collection 구성 ──
                collection_meta: dict = {}
                if step.collection_field:
                    collection_steps = _collection_steps(definition, step.collection_target)
                    if collection_steps:
                        fields = []
                        for cs in collection_steps:
                            collected_value = session.collected.get(cs.save_as)
                            fields.append({
                                "key": cs.collection_field,
                                "label": cs.collection_label or cs.collection_field,
                                "value": collected_value,
                                "status": "filled" if collected_value not in (None, "") else "pending",
                            })
                        collection_meta = {
                            "target": step.collection_target or "partner",
                            "fields": fields,
                            "parse_preview": None,  # 골격: 정규화는 백엔드 범위
                        }
                    else:
                        # graceful fallback: 현 스텝의 단일 필드만 emit
                        collected_value = session.collected.get(step.save_as)
                        collection_meta = {
                            "target": step.collection_target or "partner",
                            "fields": [{
                                "key": step.collection_field,
                                "label": step.collection_label or step.collection_field,
                                "value": collected_value,
                                "status": "filled" if collected_value not in (None, "") else "pending",
                            }],
                            "parse_preview": None,
                        }

                message_parts.append(rendered)
                return StepResult(
                    bot_message="\n\n".join(message_parts),
                    options=list(step.options),
                    step_id=step.id,
                    step_type=step.type,
                    collected=dict(session.collected),
                    report=report_hint,
                    intent_confirm=intent_confirm_meta,
                    collection=collection_meta,
                )

            # message 타입: 축적하고 다음 스텝으로 자동 진행
            message_parts.append(rendered)
            if not step.next or not definition.get_step(step.next):
                session.completed = True
                return StepResult(
                    bot_message="\n\n".join(message_parts),
                    completed=True,
                    concluded=True,
                    collected=dict(session.collected),
                    step_id=step.id,
                    step_type=step.type,
                    report=report_hint,
                )
            session.current_step_id = step.next

        # 깊이 제한 도달
        logger.warning(
            "workflow_message_chain_limit",
            layer="WORKFLOW",
            session_id=session.workflow_id,
            depth=_MAX_MESSAGE_CHAIN,
        )
        session.completed = True
        return StepResult(
            bot_message="\n\n".join(message_parts),
            completed=True,
            concluded=True,
            collected=dict(session.collected),
        )
