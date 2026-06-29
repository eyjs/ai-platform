"""WorkflowExecutorMixin — 워크플로우 모드 실행 메서드 모음.

graph_executor.py 분할 산출물. GraphExecutor MRO 상속 경로:
  GraphExecutor(WorkflowExecutorMixin, DeterministicExecutorMixin, AgenticExecutorMixin)
"""

from typing import AsyncIterator, Optional

from src.domain.execution_plan import ExecutionPlan
from src.domain.models import AgentMode, AgentResponse, TraceInfo
from src.observability.logging import get_logger
from src.workflow.engine import StepResult

logger = get_logger(__name__)


class WorkflowExecutorMixin:
    """워크플로우 모드(_execute_workflow / _stream_workflow / _run_workflow_step / _step_result_to_response)."""

    async def _execute_workflow(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
    ) -> AgentResponse:
        """워크플로우 모드 실행. StepResult → AgentResponse 변환."""
        if not self._workflow_engine:
            logger.warning("workflow_engine_missing, falling back to deterministic")
            return AgentResponse(
                answer="워크플로우 엔진이 초기화되지 않았습니다.",
                sources=[],
                trace=TraceInfo(mode="workflow"),
            )

        step_result = await self._run_workflow_step(question, plan, session_id)
        return self._step_result_to_response(step_result, plan)

    async def _stream_workflow(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """워크플로우 모드 스트리밍. 워크플로우는 즉시 응답이므로 한 번에 전송."""
        if not self._workflow_engine:
            yield {"type": "token", "data": "워크플로우 엔진이 초기화되지 않았습니다."}
            yield {"type": "done", "data": {"tools_called": [], "sources": []}}
            return

        step_result = await self._run_workflow_step(question, plan, session_id)

        yield {"type": "trace", "data": {
            "step": "workflow",
            "workflow_id": plan.workflow_id,
            "step_id": step_result.step_id,
            "step_type": step_result.step_type,
            "completed": step_result.completed,
            "escaped": step_result.escaped,
        }}
        # 선택지가 있으면 메시지에 번호 목록 추가
        message = step_result.bot_message
        if step_result.options:
            options_text = "\n".join(
                f"{i+1}. {opt}" for i, opt in enumerate(step_result.options)
            )
            message = f"{message}\n\n{options_text}"
        # 워크플로우 진행 중이면 나가기 안내 추가
        if not step_result.completed and not step_result.escaped:
            message += '\n\n_(\"나가기\" 또는 \"취소\"를 입력하면 워크플로우를 종료합니다)_'
        yield {"type": "token", "data": message}
        # 워크플로우 경로 usage: StepResult에 usage 정보가 있으면 포함 (best-effort)
        workflow_done_data: dict = {
            "tools_called": [],
            "sources": [],
            "workflow": {
                "options": step_result.options,
                "step_id": step_result.step_id,
                "step_type": step_result.step_type,
                "collected": step_result.collected,
                "completed": step_result.completed,
                "escaped": step_result.escaped,
                "report": step_result.report,
                # ── 신규(v2): 구조 신호 (saju 구조-우선 매핑) ──
                "intent_confirm": step_result.intent_confirm or None,
                "collection": step_result.collection or None,  # 수집스텝: 빌더가 채움. 빈dict→None
                "concluded": step_result.concluded,
            },
        }
        _wf_usage = getattr(step_result, "usage", None)
        if _wf_usage and isinstance(_wf_usage, dict) and any(_wf_usage.values()):
            workflow_done_data["usage"] = _wf_usage
        yield {"type": "done", "data": workflow_done_data}

    async def _run_workflow_step(
        self,
        question: str,
        plan: ExecutionPlan,
        session_id: str,
    ) -> StepResult:
        """워크플로우 시작 또는 진행."""
        engine = self._workflow_engine
        session = await engine.get_session(session_id)

        if not session or session.completed:
            # 새 워크플로우 시작
            workflow_id = plan.workflow_id
            if not workflow_id:
                return StepResult(
                    bot_message="워크플로우 ID가 지정되지 않았습니다.",
                    completed=True,
                )
            logger.info(
                "workflow_start_via_chat",
                layer="AGENT",
                workflow_id=workflow_id,
                session_id=session_id,
            )
            return await engine.start(
                workflow_id, session_id,
                context_adapter=plan.context_adapter,
                cache_padding_text=plan.cache_padding_text,
            )

        # 기존 세션 진행
        return await engine.advance(session_id, question)

    @staticmethod
    def _step_result_to_response(
        step_result: StepResult,
        plan: ExecutionPlan,
    ) -> AgentResponse:
        """StepResult를 AgentResponse로 변환."""
        # 선택지가 있으면 메시지에 번호 목록 추가
        answer = step_result.bot_message
        if step_result.options:
            options_text = "\n".join(
                f"{i+1}. {opt}" for i, opt in enumerate(step_result.options)
            )
            answer = f"{answer}\n\n{options_text}"

        return AgentResponse(
            answer=answer,
            sources=[],
            trace=TraceInfo(
                question_type=plan.question_type.value if plan.question_type else "",
                mode="workflow",
                tools_called=[],
                router_decision={
                    "workflow_id": plan.workflow_id,
                    "step_id": step_result.step_id,
                    "step_type": step_result.step_type,
                    "completed": step_result.completed,
                    "escaped": step_result.escaped,
                },
            ),
        )
