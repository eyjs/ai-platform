"""Workflow Graph Builder: WorkflowDefinition → LangGraph StateGraph 동적 컴파일.

store.py가 로딩한 WorkflowDefinition을 받아 LangGraph StateGraph로 **동적 컴파일**한다.
step 타입 6종(message/dynamic/input/select/confirm/action)을 노드+엣지로 기계적으로 매핑하며,
특정 workflow_id/profile을 하드코딩하지 않는다 — 절대규칙 1번 구현의 핵심.

escape/back/validation/branches 분기는 legacy engine.py 로직과 동등하게 재현한다.
step_logic.py 헬퍼(_resolve_next/_validate_input 등)를 그대로 재사용한다.
step_executors.py(generate_dynamic/execute_action_step)를 변경 없이 호출한다.
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from src.observability.logging import get_logger
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.graph_state import WorkflowGraphState
from src.workflow.state import WorkflowSession
from src.workflow.step_executors import execute_action_step, generate_dynamic
from src.workflow.step_logic import (
    _collection_steps,
    _resolve_next,
    _validate_input,
    _visible_ctx_lines,
)
from src.workflow.step_result import StepResult
from src.workflow.template import render_template

logger = get_logger(__name__)

# 전역 escape/back 키워드 — engine.py:46-49와 동등
_ESCAPE_KEYWORDS = {"취소", "처음으로", "나가기", "중단", "그만", "exit", "cancel", "quit"}
_BACK_KEYWORDS = {"뒤로", "이전", "돌아가기", "back", "prev"}

# message/dynamic 자동 전이 체인 깊이 제한 — engine.py:42 _MAX_MESSAGE_CHAIN과 동등
_MAX_MESSAGE_CHAIN = 10


def _step_result_to_dict(result: StepResult) -> dict:
    """StepResult dataclass를 dict로 변환한다 (state 채널 직렬화용)."""
    return {
        "bot_message": result.bot_message,
        "options": result.options,
        "step_id": result.step_id,
        "step_type": result.step_type,
        "collected": result.collected,
        "completed": result.completed,
        "escaped": result.escaped,
        "action_result": result.action_result,
        "report": result.report,
        "intent_confirm": result.intent_confirm,
        "collection": result.collection,
        "concluded": result.concluded,
    }


def _make_escape_result(collected: dict) -> dict:
    """escape(이탈) StepResult dict를 생성한다 — engine.py:517-523 동등."""
    return _step_result_to_dict(StepResult(
        bot_message="워크플로우가 취소되었습니다. 다른 질문이 있으시면 말씀해주세요.",
        completed=True,
        escaped=True,
        concluded=True,
        collected=dict(collected),
    ))


def _check_escape(user_input: str, definition: WorkflowDefinition) -> bool:
    """이탈 키워드 감지 — engine.py:483-523 동등."""
    if definition.escape_policy != "allow":
        return False
    normalized = user_input.strip().lower()
    # 워크플로우별 escape_keywords 우선, 없으면 전역 폴백 (engine.py:499-504)
    keywords = (
        {kw.lower() for kw in definition.escape_keywords}
        if definition.escape_keywords
        else _ESCAPE_KEYWORDS
    )
    return normalized in keywords


def _build_confirm_prompt(step: WorkflowStep, collected: dict) -> str:
    """confirm 스텝 요약 메시지를 조립한다 — engine.py:623-632 동등."""
    rendered = render_template(step.prompt, collected)
    summary_lines = [f"- {k}: {v}" for k, v in collected.items()]
    if summary_lines:
        rendered = f"{rendered}\n\n" + "\n".join(summary_lines)
    return rendered


def _build_intent_confirm_meta(step: WorkflowStep) -> dict:
    """confirm 스텝 intent_confirm 메타를 조립한다 — engine.py:626-632 동등."""
    return {
        "intent": step.intent or "",
        "yes_label": step.confirm_yes_label or "응",
        "no_label": step.confirm_no_label or "아니",
    }


def _build_collection_meta(step: WorkflowStep, definition: WorkflowDefinition, collected: dict) -> dict:
    """input/select 수집 스텝의 collection 메타를 조립한다 — engine.py:634-665 동등."""
    if not step.collection_field:
        return {}
    collection_steps = _collection_steps(definition, step.collection_target)
    if collection_steps:
        fields = []
        for cs in collection_steps:
            value = collected.get(cs.save_as)
            fields.append({
                "key": cs.collection_field,
                "label": cs.collection_label or cs.collection_field,
                "value": value,
                "status": "filled" if value not in (None, "") else "pending",
            })
        return {
            "target": step.collection_target or "partner",
            "fields": fields,
            "parse_preview": None,
        }
    # graceful fallback: 현 스텝의 단일 필드만 emit
    value = collected.get(step.save_as)
    return {
        "target": step.collection_target or "partner",
        "fields": [{
            "key": step.collection_field,
            "label": step.collection_label or step.collection_field,
            "value": value,
            "status": "filled" if value not in (None, "") else "pending",
        }],
        "parse_preview": None,
    }


class WorkflowGraphBuilder:
    """WorkflowDefinition → CompiledStateGraph 동적 빌더.

    생성자에서 의존성을 한 번만 주입받고, get_graph()로 컴파일된 그래프를 반환한다.
    동일 workflow_id에 대한 컴파일은 캐시하여 재사용한다.

    절대규칙 1·3 준수:
    - 빌더 코드 어디에도 `if workflow_id == ...` / `if profile == ...` 없음
    - step 타입 메타만 보고 기계적으로 그래프를 조립함
    """

    def __init__(
        self,
        store: Any,  # WorkflowStore — 타입 import 없이 duck-typing
        llm: Any | None = None,
        context_adapters: dict | None = None,
        classifier: Any | None = None,
        action_endpoint_default: str | None = None,
        action_headers_default: dict | None = None,
    ) -> None:
        self._store = store
        self._llm = llm
        self._context_adapters = context_adapters or {}
        self._classifier = classifier
        self._action_endpoint_default = action_endpoint_default
        self._action_headers_default = action_headers_default or {}
        # workflow_id → CompiledStateGraph 캐시 (빌더 인스턴스별)
        self._cache: dict[str, Any] = {}

    def get_graph(self, workflow_id: str, checkpointer: Any) -> Any:
        """CompiledStateGraph를 반환한다. 캐시 미스 시 컴파일 후 캐시한다.

        checkpointer를 호출 시 주입 — 같은 그래프 정의에 saver만 바뀔 수 있도록.
        (컴파일 캐시는 definition 변화 기반이므로 checkpointer는 캐시 키에 포함하지 않음)
        """
        if workflow_id not in self._cache:
            definition = self._store.get(workflow_id)
            if not definition:
                raise ValueError(f"워크플로우를 찾을 수 없습니다: {workflow_id}")
            compiled = self._build(definition).compile(checkpointer=checkpointer)
            self._cache[workflow_id] = compiled
            logger.info(
                "workflow_graph_compiled",
                layer="WORKFLOW",
                workflow_id=workflow_id,
                steps=len(definition.steps),
            )
        return self._cache[workflow_id]

    def invalidate(self, workflow_id: str | None = None) -> None:
        """컴파일 캐시를 무효화한다 — Admin 정의 수정 후 재컴파일용.

        store.invalidate_cache()와 동기화해 호출해야 한다.
        workflow_id가 None이면 전체 캐시 클리어.
        """
        if workflow_id:
            self._cache.pop(workflow_id, None)
        else:
            self._cache.clear()
        logger.info(
            "workflow_graph_cache_invalidated",
            layer="WORKFLOW",
            workflow_id=workflow_id or "ALL",
        )

    def _build(self, definition: WorkflowDefinition) -> StateGraph:
        """WorkflowDefinition → StateGraph(미컴파일) 변환.

        각 step을 노드로 추가하고, step 타입에 따라 엣지를 연결한다.
        """
        graph = StateGraph(WorkflowGraphState)

        for step in definition.steps:
            node_fn = self._make_node(step, definition)
            graph.add_node(step.id, node_fn)

        # START → entry step
        entry = definition.entry_step_id
        graph.add_edge(START, entry)

        # 각 step의 엣지 설정
        for step in definition.steps:
            self._add_edges(graph, step, definition)

        return graph

    # ──────────────────────────────────────────────────
    # 노드 팩토리: 클로저로 step + 의존성을 캡처
    # ──────────────────────────────────────────────────

    def _make_node(self, step: WorkflowStep, definition: WorkflowDefinition):
        """step 타입에 맞는 노드 함수를 생성한다."""
        if step.type == "message":
            return self._make_message_node(step, definition)
        if step.type == "dynamic":
            return self._make_dynamic_node(step, definition)
        if step.type == "action":
            return self._make_action_node(step, definition)
        if step.type in ("input", "select", "confirm"):
            return self._make_interactive_node(step, definition)
        # 알 수 없는 타입 → 에러 결과 후 END (조용히 삼키지 않음)
        return self._make_unknown_type_node(step)

    def _make_message_node(self, step: WorkflowStep, definition: WorkflowDefinition):
        """message 노드: prompt 렌더 후 message_parts에 누적, next로 자동전이.

        message-chain 체인은 LangGraph 엣지 연결(add_edge)로 표현하며,
        재귀가 없으므로 recursion_limit를 소진하지 않는다.
        """
        async def node(state: WorkflowGraphState) -> dict:
            collected = state.get("collected") or {}
            rendered = render_template(step.prompt, collected)
            parts = list(state.get("message_parts") or [])
            parts.append(rendered)
            report_hint = step.report or state.get("report_hint") or ""
            updates: dict = {
                "message_parts": parts,
                "report_hint": report_hint,
                "current_step_id": step.id,
            }
            # next 없거나 미존재 → END 처리는 엣지에서 수행
            return updates

        return node

    def _make_dynamic_node(self, step: WorkflowStep, definition: WorkflowDefinition):
        """dynamic 노드: generate_dynamic 호출 후 message_parts 누적, next로 자동전이."""
        llm = self._llm
        context_adapters = self._context_adapters

        async def node(state: WorkflowGraphState) -> dict:
            collected = state.get("collected") or {}
            report_hint = step.report or state.get("report_hint") or ""
            insight = await generate_dynamic(
                step, collected,
                llm=llm,
                context_adapters=context_adapters,
            )
            parts = list(state.get("message_parts") or [])
            if insight:
                parts.append(insight)
            return {
                "message_parts": parts,
                "report_hint": report_hint,
                "current_step_id": step.id,
            }

        return node

    def _make_action_node(self, step: WorkflowStep, definition: WorkflowDefinition):
        """action 노드: execute_action_step 호출 후 collected/callback_response 회수.

        execute_action_step은 session.collected/session.callback_response를 mutate
        하므로(step_executors.py:170-174), 경량 WorkflowSession 어댑터를 만들어
        넘기고 반환 후 state로 회수한다 — 불변성은 노드 반환 dict로 충족.
        """
        action_endpoint = self._action_endpoint_default
        action_headers = self._action_headers_default

        async def node(state: WorkflowGraphState) -> dict:
            collected = dict(state.get("collected") or {})
            message_parts = list(state.get("message_parts") or [])
            report_hint = step.report or state.get("report_hint") or ""

            # 경량 어댑터 세션 — execute_action_step이 mutate할 대상
            adapter_session = WorkflowSession(
                workflow_id=state.get("workflow_id", ""),
                current_step_id=step.id,
                collected=collected,
                callback_response=dict(state.get("callback_response") or {}),
            )

            action_result = await execute_action_step(
                step, adapter_session,
                None,  # action_client: T4에서 연결 — 빌더 단독 테스트에선 None
                action_endpoint,
                action_headers,
            )

            # mutate된 collected/callback_response 회수
            new_collected = dict(adapter_session.collected)
            new_callback = dict(adapter_session.callback_response)

            if action_result.bot_message:
                message_parts.append(action_result.bot_message)

            last_result = _step_result_to_dict(action_result)
            last_result["report"] = report_hint
            last_result["message_parts_snapshot"] = list(message_parts)

            updates = {
                "collected": new_collected,
                "callback_response": new_callback,
                "message_parts": message_parts,
                "report_hint": report_hint,
                "current_step_id": step.id,
                "last_result": last_result,
            }

            # 실패(completed=True) or next 없음 → END 신호
            if action_result.completed or not step.next:
                updates["completed"] = True
                full_msg = "\n\n".join(message_parts)
                last_result["bot_message"] = full_msg
                last_result["completed"] = True
                last_result["concluded"] = True
                updates["last_result"] = last_result
                return updates

            # 성공 + next → message_parts carry 후 next 노드로
            return updates

        return node

    def _make_interactive_node(self, step: WorkflowStep, definition: WorkflowDefinition):
        """input/select/confirm 노드: interrupt로 사용자 입력 대기 후 분기 처리.

        interrupt() 호출 직전에 last_result(StepResult 동등 dict)를 구성해
        payload로 전달한다 — 프론트엔드/caller가 prompt/options를 소비할 수 있도록.

        재개(Command(resume=user_input)) 시 escape/back/validation/resolve_next/
        classifier 분기를 legacy engine._advance_inner와 동등하게 처리한다.
        """
        classifier = self._classifier
        max_retries = definition.max_retries

        async def node(state: WorkflowGraphState) -> dict | Command:
            collected = dict(state.get("collected") or {})
            message_parts = list(state.get("message_parts") or [])
            retry_count = state.get("retry_count") or 0
            step_history = list(state.get("step_history") or [])
            report_hint = step.report or state.get("report_hint") or ""

            # ── interrupt 전 StepResult 조립 (engine.py:621-677 동등) ──
            rendered = render_template(step.prompt, collected)
            intent_confirm_meta: dict = {}
            collection_meta: dict = {}

            if step.type == "confirm":
                rendered = _build_confirm_prompt(step, collected)
                intent_confirm_meta = _build_intent_confirm_meta(step)
            elif step.collection_field:
                collection_meta = _build_collection_meta(step, definition, collected)

            interrupt_payload = {
                "bot_message": "\n\n".join(message_parts + [rendered]) if message_parts else rendered,
                "options": list(step.options),
                "step_id": step.id,
                "step_type": step.type,
                "collected": dict(collected),
                "report": report_hint,
                "intent_confirm": intent_confirm_meta,
                "collection": collection_meta,
            }

            # ── interrupt: 사용자 입력 대기 ──
            user_input: str = interrupt(interrupt_payload)

            # ── 재개 후 처리 ──
            # 1. escape 감지 (engine.py:483-523 동등)
            if _check_escape(user_input, definition):
                logger.info(
                    "workflow_escape",
                    layer="WORKFLOW",
                    workflow_id=state.get("workflow_id"),
                    trigger=user_input.strip().lower(),
                )
                escape_result = _make_escape_result(collected)
                return Command(goto=END, update={
                    "completed": True,
                    "collected": collected,
                    "last_result": escape_result,
                    "message_parts": [],
                    "step_history": step_history,
                })

            # 2. 뒤로가기 감지 (engine.py:260-289 동등)
            if user_input.strip().lower() in _BACK_KEYWORDS or any(
                kw in user_input for kw in _BACK_KEYWORDS
            ):
                if step_history:
                    prev_step_id = step_history[-1]
                    new_history = step_history[:-1]
                    prev_step = definition.get_step(prev_step_id)
                    new_collected = dict(collected)
                    if prev_step and prev_step.save_as and prev_step.save_as in new_collected:
                        del new_collected[prev_step.save_as]
                    logger.info(
                        "workflow_back",
                        layer="WORKFLOW",
                        from_step=step.id,
                        to_step=prev_step_id,
                    )
                    return Command(goto=prev_step_id, update={
                        "current_step_id": prev_step_id,
                        "collected": new_collected,
                        "step_history": new_history,
                        "retry_count": 0,
                        "message_parts": [],
                    })
                else:
                    # 첫 번째 단계 — 뒤로 갈 수 없음, 같은 스텝 재프롬프트
                    no_back_result = {
                        "bot_message": "첫 번째 단계입니다. 더 이상 뒤로 갈 수 없습니다.",
                        "options": list(step.options),
                        "step_id": step.id,
                        "step_type": step.type,
                        "collected": dict(collected),
                        "completed": False,
                        "escaped": False,
                        "action_result": {},
                        "report": report_hint,
                        "intent_confirm": intent_confirm_meta,
                        "collection": collection_meta,
                        "concluded": False,
                    }
                    return Command(goto=step.id, update={
                        "last_result": no_back_result,
                        "retry_count": retry_count,
                        "message_parts": [],
                    })

            # 3. 입력 검증 (engine.py:292-317 동등)
            validation_error = _validate_input(step, user_input)
            if validation_error:
                new_retry = retry_count + 1
                if new_retry >= max_retries:
                    logger.info(
                        "workflow_retry_limit",
                        layer="WORKFLOW",
                        step_id=step.id,
                        retries=new_retry,
                    )
                    timeout_result = _step_result_to_dict(StepResult(
                        bot_message="입력이 지연되어 진행을 취소합니다. 다른 도움이 필요하시면 말씀해주세요.",
                        completed=True,
                        escaped=True,
                        concluded=True,
                        collected=dict(collected),
                    ))
                    return Command(goto=END, update={
                        "completed": True,
                        "retry_count": new_retry,
                        "last_result": timeout_result,
                        "message_parts": [],
                    })
                # 재프롬프트 (같은 스텝으로)
                reprompt_result = {
                    "bot_message": validation_error,
                    "options": list(step.options),
                    "step_id": step.id,
                    "step_type": step.type,
                    "collected": dict(collected),
                    "completed": False,
                    "escaped": False,
                    "action_result": {},
                    "report": report_hint,
                    "intent_confirm": intent_confirm_meta,
                    "collection": collection_meta,
                    "concluded": False,
                }
                return Command(goto=step.id, update={
                    "retry_count": new_retry,
                    "last_result": reprompt_result,
                    "message_parts": [],
                })

            # 4. 데이터 수집 (engine.py:320-321 동등)
            new_collected = dict(collected)
            if step.save_as:
                new_collected[step.save_as] = user_input

            # 5. 다음 스텝 결정 (engine.py:324 + _resolve_next)
            next_step_id = _resolve_next(step, user_input)

            # 6. 미매칭 → 의미 분류기 시도 (engine.py:328-347 동등)
            if not next_step_id and step.branches and classifier:
                from src.domain.classifier import Candidate
                candidates = [Candidate(label=k) for k in step.branches]
                ctx = render_template(step.prompt, new_collected)
                ctx_lines = _visible_ctx_lines(new_collected)
                if ctx_lines:
                    ctx = f"{ctx}\n[지금까지 파악된 정보]\n" + "\n".join(ctx_lines)
                decision = await classifier.classify(
                    user_input, candidates, context=ctx,
                )
                if decision.label and decision.label in step.branches:
                    next_step_id = step.branches[decision.label]
                    if step.save_as:
                        new_collected[step.save_as] = decision.label
                    logger.info(
                        "workflow_branch_llm_classified",
                        layer="WORKFLOW",
                        step_id=step.id,
                        label=decision.label,
                    )

            # 7. select/branch 미매칭 → 재프롬프트 (engine.py:353-385 동등)
            if not next_step_id and step.branches:
                # 잘못 담긴 미매칭 입력 롤백
                if step.save_as and step.save_as in new_collected:
                    del new_collected[step.save_as]
                new_retry = retry_count + 1
                if new_retry >= max_retries:
                    logger.info(
                        "workflow_select_no_match_escape",
                        layer="WORKFLOW",
                        step_id=step.id,
                        retries=new_retry,
                    )
                    no_match_result = _step_result_to_dict(StepResult(
                        bot_message="여러 번 이해하지 못했어요. 잠시 후 다시 시도해 주세요.",
                        completed=True,
                        escaped=True,
                        concluded=True,
                        collected=dict(new_collected),
                        step_id=step.id,
                        step_type=step.type,
                    ))
                    return Command(goto=END, update={
                        "completed": True,
                        "collected": new_collected,
                        "retry_count": new_retry,
                        "last_result": no_match_result,
                        "message_parts": [],
                    })
                logger.info(
                    "workflow_select_no_match_reprompt",
                    layer="WORKFLOW",
                    step_id=step.id,
                    retries=new_retry,
                    user_input=user_input[:50],
                )
                reprompt_result = {
                    "bot_message": render_template(step.prompt, new_collected),
                    "options": list(step.options),
                    "step_id": step.id,
                    "step_type": step.type,
                    "collected": dict(new_collected),
                    "completed": False,
                    "escaped": False,
                    "action_result": {},
                    "report": report_hint,
                    "intent_confirm": intent_confirm_meta,
                    "collection": collection_meta,
                    "concluded": False,
                }
                return Command(goto=step.id, update={
                    "collected": new_collected,
                    "retry_count": new_retry,
                    "last_result": reprompt_result,
                    "message_parts": [],
                })

            # 8. 정상 진행 확정 (engine.py:387-426 동등)
            new_history = list(step_history) + [step.id]

            # next 없음 → END (말단 스텝)
            if not next_step_id:
                complete_result = _step_result_to_dict(StepResult(
                    bot_message="워크플로우가 완료되었습니다.",
                    completed=True,
                    concluded=True,
                    collected=dict(new_collected),
                    step_id=step.id,
                    step_type="complete",
                ))
                return Command(goto=END, update={
                    "completed": True,
                    "collected": new_collected,
                    "step_history": new_history,
                    "retry_count": 0,
                    "last_result": complete_result,
                    "message_parts": [],
                })

            # next step 미존재 → 에러 후 END (engine.py:411-419 동등)
            if not definition.get_step(next_step_id):
                error_result = _step_result_to_dict(StepResult(
                    bot_message=f"다음 스텝({next_step_id})을 찾을 수 없습니다.",
                    completed=True,
                    concluded=True,
                    collected=dict(new_collected),
                ))
                return Command(goto=END, update={
                    "completed": True,
                    "collected": new_collected,
                    "step_history": new_history,
                    "retry_count": 0,
                    "last_result": error_result,
                    "message_parts": [],
                })

            # 정상 다음 스텝으로 이동
            logger.info(
                "workflow_advance",
                layer="WORKFLOW",
                from_step=step.id,
                to_step=next_step_id,
                user_input=user_input[:50],
            )
            return Command(goto=next_step_id, update={
                "current_step_id": next_step_id,
                "collected": new_collected,
                "step_history": new_history,
                "retry_count": 0,
                "message_parts": [],  # 새 스텝 진입 시 리셋
            })

        return node

    def _make_unknown_type_node(self, step: WorkflowStep):
        """알 수 없는 step 타입에 대한 에러 노드."""
        async def node(state: WorkflowGraphState) -> dict:
            logger.error(
                "workflow_unknown_step_type",
                layer="WORKFLOW",
                step_id=step.id,
                step_type=step.type,
            )
            error_result = _step_result_to_dict(StepResult(
                bot_message=f"알 수 없는 스텝 타입입니다: {step.type}",
                completed=True,
                concluded=True,
                collected=dict(state.get("collected") or {}),
            ))
            return {
                "completed": True,
                "last_result": error_result,
            }

        return node

    # ──────────────────────────────────────────────────
    # 엣지 설정: step 타입에 따라 엣지를 연결
    # ──────────────────────────────────────────────────

    def _add_edges(self, graph: StateGraph, step: WorkflowStep, definition: WorkflowDefinition) -> None:
        """step 타입에 맞는 엣지를 그래프에 추가한다."""
        if step.type == "message":
            self._add_message_edges(graph, step, definition)
        elif step.type == "dynamic":
            self._add_dynamic_edges(graph, step, definition)
        elif step.type == "action":
            self._add_action_edges(graph, step, definition)
        elif step.type in ("input", "select", "confirm"):
            # interactive 노드는 Command(goto=...) 로 자체 라우팅 — 추가 엣지 불필요
            # (노드가 Command를 반환하면 LangGraph가 그 goto로 이동)
            pass
        elif step.type == "unknown":
            graph.add_edge(step.id, END)

    def _add_message_edges(self, graph: StateGraph, step: WorkflowStep, definition: WorkflowDefinition) -> None:
        """message 노드: next 있으면 next로, 없으면 END."""
        if step.next and definition.get_step(step.next):
            graph.add_edge(step.id, step.next)
        else:
            graph.add_edge(step.id, END)

    def _add_dynamic_edges(self, graph: StateGraph, step: WorkflowStep, definition: WorkflowDefinition) -> None:
        """dynamic 노드: next 있으면 next로, 없으면 END (engine.py:575-587 동등)."""
        if step.next and definition.get_step(step.next):
            graph.add_edge(step.id, step.next)
        else:
            graph.add_edge(step.id, END)

    def _add_action_edges(self, graph: StateGraph, step: WorkflowStep, definition: WorkflowDefinition) -> None:
        """action 노드: completed=True → END, 성공+next → next (conditional edge).

        노드가 state["completed"]=True를 설정하거나 next가 없으면 END로 라우팅.
        """
        if not step.next or not definition.get_step(step.next):
            graph.add_edge(step.id, END)
        else:
            next_id = step.next

            def route_action(state: WorkflowGraphState) -> str:
                if state.get("completed"):
                    return END
                return next_id

            graph.add_conditional_edges(step.id, route_action, {END: END, next_id: next_id})
