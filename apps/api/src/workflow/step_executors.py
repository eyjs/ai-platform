"""Workflow step-type 실행기: dynamic(LLM 통찰) / action(외부 API).

엔진(_process_current_step)이 위임하는 per-type 실행 로직. 의존성을 명시 인자로 받는
자유 함수로, 엔진 클래스의 상태에 직접 결합하지 않는다(테스트·재사용 용이).
"""

from __future__ import annotations

from datetime import datetime

from src.common.cache_padding import pad_to_min
from src.observability.logging import get_logger
from src.workflow.action_client import WorkflowActionError
from src.workflow.definition import WorkflowStep
from src.workflow.state import WorkflowSession
from src.workflow.step_logic import _visible_ctx_lines
from src.workflow.step_result import StepResult
from src.workflow.template import render_template

logger = get_logger(__name__)


async def generate_dynamic(step, collected: dict, *, llm, context_adapters: dict) -> str:
    """dynamic 스텝: LLM이 캐릭터 페르소나(step.system)로 collected + (어댑터가
    제공하는) 도메인 컨텍스트를 근거로 통찰을 생성한다.

    도메인 데이터 enrichment는 세션에 바인딩된 ContextAdapter가 담당한다.
    엔진은 어댑터가 돌려준 블록을 프롬프트에 그대로 이어붙일 뿐 도메인을 알지 않는다.

    Prompt Caching 분리 (task-101):
    - cacheable_system: persona(step.system) + grounding(adapter.enrich) — 세션 안정 바이트
    - volatile_system: 오늘 날짜 — 매일 변하므로 캐시 경계 밖
    - user_prompt: collected 정보 + per-turn 지시

    LLM 미주입/실패 시 step.prompt 템플릿을 정적 폴백으로 사용(워크플로우 진행 보장).
    """
    fallback = render_template(step.prompt, collected)
    if not llm:
        return fallback

    # 세션에 바인딩된 어댑터로 도메인 컨텍스트를 보강한다(없으면 grounding 없이 진행).
    # grounding은 세션 내 안정 — cacheable_system에 포함해 캐시 히트를 극대화한다.
    grounding_block = ""
    adapter = context_adapters.get(collected.get("_adapter") or "")
    if adapter:
        try:
            extra = await adapter.enrich(collected)
            grounding_block = "".join(extra.values())
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "context_adapter_enrich_failed",
                layer="WORKFLOW",
                step_id=step.id,
                adapter=collected.get("_adapter"),
                error=str(e),
            )

    persona = render_template(step.system, collected)

    # cacheable_system: persona + grounding — 식별자/timestamp 제외(캐시 안정성).
    # Anthropic Haiku 캐시 최소 4096 토큰 미달 시 구조화된 지침 패딩을 추가한다.
    cacheable_parts = [persona]
    if grounding_block:
        cacheable_parts.append(grounding_block)
    cacheable_system = "\n\n".join(p for p in cacheable_parts if p)

    # 4096 토큰 미달 보정 — 캐시 효과 확보. 도메인 배경 텍스트(Profile.cache_padding_text)가
    # 세션에 바인딩돼 있으면 filler로, 없으면 도메인 중립 여백으로 채운다(엔진 도메인 무지).
    padding_filler = collected.get("_pad_text") or ""
    if not isinstance(padding_filler, str):
        padding_filler = ""
    cacheable_system = pad_to_min(cacheable_system, filler=padding_filler)

    # volatile_system: 오늘 날짜 — 날짜가 cacheable에 들어가면 매일 캐시 무효화 발생.
    today = datetime.now()
    volatile_system = (
        f"[오늘 날짜] {today.year}년 {today.month}월 {today.day}일. "
        f"'올해'는 {today.year}년, '내년'은 {today.year + 1}년이다."
    )

    # user_prompt: per-turn 정보 (collected) — 캐시 밖.
    # 식별자/내부 키는 표시에서 제외(session_id + 어댑터 등록 _hidden_keys + _-prefix).
    ctx_lines = _visible_ctx_lines(collected)
    ctx = "\n".join(ctx_lines) if ctx_lines else "(아직 정보 없음)"
    user_prompt = (
        f"{render_template(step.prompt, collected)}\n\n"
        f"[지금까지 대화에서 파악된 내담자 정보]\n{ctx}\n\n"
        f"위 지시와 정보를 바탕으로, 캐릭터 톤을 유지한 짧은 메시지만 출력하세요. "
        f"설명·메타발화·따옴표 없이 대사만."
    )
    try:
        text = await llm.generate(
            user_prompt,
            cacheable_system=cacheable_system,
            volatile_system=volatile_system,
        )
        text = (text or "").strip()
        return text or fallback
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "dynamic_step_llm_failed",
            layer="WORKFLOW",
            step_id=step.id,
            error=str(e),
        )
        return fallback


async def execute_action_step(
    step: WorkflowStep,
    session: WorkflowSession,
    action_client,
    profile_endpoint: str | None = None,
    profile_headers: dict | None = None,
) -> StepResult:
    """action step을 실행한다.

    1. endpoint: step.endpoint > profile_endpoint (둘 다 없으면 에러)
    2. headers: step.headers_template + profile_headers 병합
    3. payload: step.payload_template
    4. 호출 성공 -> on_success_message + 다음 스텝 진행
    5. 호출 실패 -> on_error_message + 워크플로우 종료
    """
    if not action_client:
        logger.error(
            "action_step_no_client",
            layer="WORKFLOW",
            step_id=step.id,
        )
        return StepResult(
            bot_message=step.on_error_message or "외부 연동 기능이 비활성화되어 있습니다.",
            step_id=step.id,
            step_type="action",
            collected=dict(session.collected),
            completed=True,
        )

    # 엔드포인트 결정: step > profile
    endpoint = step.endpoint or profile_endpoint
    if not endpoint:
        logger.error(
            "action_step_no_endpoint",
            layer="WORKFLOW",
            step_id=step.id,
        )
        return StepResult(
            bot_message=step.on_error_message or "외부 API 엔드포인트가 설정되지 않았습니다.",
            step_id=step.id,
            step_type="action",
            collected=dict(session.collected),
            completed=True,
        )

    # 헤더 병합: profile 기본값 + step 오버라이드
    merged_headers = dict(profile_headers or {})
    if step.headers_template:
        merged_headers.update(step.headers_template)

    try:
        response_data = await action_client.call(
            endpoint=endpoint,
            method=step.http_method,
            headers=merged_headers if merged_headers else None,
            payload=step.payload_template if step.payload_template else None,
            timeout=step.timeout_seconds,
            collected=session.collected,
        )

        # 응답 데이터를 세션에 저장 (save_as가 있으면)
        if step.save_as:
            session.collected[step.save_as] = response_data

        # 콜백 응답도 세션에 기록
        session.callback_response = response_data

        success_message = render_template(
            step.on_success_message or "처리가 완료되었습니다.",
            session.collected,
        )

        logger.info(
            "action_step_success",
            layer="WORKFLOW",
            step_id=step.id,
            endpoint=endpoint[:100],
        )

        return StepResult(
            bot_message=success_message,
            step_id=step.id,
            step_type="action",
            collected=dict(session.collected),
            action_result=response_data,
        )

    except WorkflowActionError as e:
        logger.warning(
            "action_step_failed",
            layer="WORKFLOW",
            step_id=step.id,
            endpoint=endpoint[:100],
            status_code=e.status_code,
            error=str(e),
        )

        error_message = render_template(
            step.on_error_message or "외부 시스템 연동 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            session.collected,
        )

        return StepResult(
            bot_message=error_message,
            step_id=step.id,
            step_type="action",
            collected=dict(session.collected),
            completed=True,
            action_result={"error": str(e), "status_code": e.status_code},
        )
