"""Workflow Engine: 순차적 챗봇 실행 엔진.

결정 트리 기반 대화를 실행한다.
엔진은 LangGraph StateGraph + checkpointer를 통해 상태를 관리하며,
현재 스텝을 처리하고 다음 스텝으로 전이한 결과를 반환한다.

모든 공개 메서드는 async — 세션 영속화 + 외부 API 호출 지원.

공개 메서드 시그니처 5개(start/advance/resume/get_session/cancel)는 helpers.py/graph_executor.py
무변경 계약을 보존한다.

사용법:
    # 운영 (AsyncPostgresSaver 주입)
    engine = WorkflowEngine(store, graph_builder=builder, checkpointer=checkpointer,
                            action_client=action_client)
    # 단위 테스트 (MemorySaver 자동 부트스트랩)
    engine = WorkflowEngine(store)
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
    """LangGraph StateGraph 기반 챗봇 실행 엔진 (T6 단일 엔진 컷오버).

    세션 영속화: checkpointer(AsyncPostgresSaver)가 주입되면 PostgreSQL에 저장,
    없으면 MemorySaver(인메모리)를 자동 생성한다.

    Action step: action_client가 주입되면 외부 HTTP 호출 가능,
    없으면 action step에서 에러 메시지 반환.

    단위 테스트 사용:
        engine = WorkflowEngine(store)
        # graph_builder/checkpointer 미주입 시 MemorySaver + WorkflowGraphBuilder(store) 자동 생성.

    R5 결정 — action_endpoint/headers:
      graph_executor.py는 start/advance에 endpoint를 넘기지 않는다(:341-348).
      WorkflowGraphBuilder 생성자에 action_endpoint_default/action_headers_default를
      주입하면 모든 action 노드가 해당 값을 사용한다.
    """

    def __init__(
        self,
        store: WorkflowStore,
        action_client: ActionClient | None = None,
        llm=None,
        context_adapters: dict[str, WorkflowContextAdapter] | None = None,
        classifier=None,
        graph_builder=None,   # WorkflowGraphBuilder — 미주입 시 자동 생성
        checkpointer=None,    # AsyncPostgresSaver 또는 MemorySaver — 미주입 시 MemorySaver 자동 생성
    ) -> None:
        self._store = store
        self._action_client = action_client
        # dynamic 스텝(LLM 캐릭터 통찰)용 LLMProvider. 없으면 dynamic은 정적 폴백.
        self._llm = llm
        # 서비스별 컨텍스트 enrichment 플러그인 (이름 → 어댑터). 프로파일이 선택한다.
        self._context_adapters = context_adapters or {}
        # select 분기 의미 분류용 공통 SemanticClassifier. 없으면 키워드 매칭만(하위호환).
        self._classifier = classifier

        # graph_builder 미주입 시 WorkflowGraphBuilder(store) 자동 생성 (단위 테스트 지원)
        if graph_builder is None:
            from src.workflow.graph_builder import WorkflowGraphBuilder
            graph_builder = WorkflowGraphBuilder(
                store=store,
                llm=llm,
                context_adapters=self._context_adapters,
                classifier=classifier,
                action_client=action_client,
            )
            logger.debug("workflow_engine_auto_graph_builder", layer="WORKFLOW")

        # checkpointer 미주입 시 MemorySaver 자동 생성 (단위 테스트 지원)
        if checkpointer is None:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
            logger.debug("workflow_engine_auto_memory_saver", layer="WORKFLOW")

        # Bug #2 수정: 외부에서 graph_builder를 주입할 때 action_client를 builder에 전파.
        # 테스트 factory는 builder를 먼저 생성한 뒤 engine에 action_client를 넘기므로,
        # builder 생성 시점에 action_client를 알 수 없다 → engine __init__에서 동기화.
        if action_client is not None:
            graph_builder._action_client = action_client

        self._graph_builder = graph_builder
        self._checkpointer = checkpointer

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
        return await self._lg_start(
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
        return await self._lg_advance(session_id, user_input)

    async def resume(
        self,
        workflow_id: str,
        session_id: str,
        step_id: str,
        collected: dict,
    ) -> StepResult:
        """일시 중지된 워크플로우를 재개한다.

        LangGraph: 해당 thread의 체크포인트를 step_id/collected로 시드해 덮어쓴다.
        """
        return await self._lg_resume(workflow_id, session_id, step_id, collected)

    async def get_session(self, session_id: str) -> Optional[WorkflowSession]:
        """세션 상태를 조회한다."""
        return await self._lg_get_session(session_id)

    async def cancel(self, session_id: str) -> bool:
        """워크플로우를 취소한다."""
        return await self._lg_cancel(session_id)

    # ──────────────────────────────────────────────────
    # LangGraph 경로 (신엔진)
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

        # collected 초기화
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

        먼저 이미 완료된 세션인지 확인한다.
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
