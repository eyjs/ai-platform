"""채팅 엔드포인트: /chat (비스트리밍), /chat/stream (SSE). 인증 필수."""

import asyncio
import json
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from src.domain.agent_context import AgentContext
from src.domain.models import AgentResponse
from src.gateway.gateway_hooks import (
    safe_enqueue, should_use_cache, try_cache_get, try_cache_put,
)
from src.gateway.models import ChatRequest, UserContext
from src.gateway.routes.helpers import (
    _authenticate,
    _check_rate_limit,
    _ChatSetup,
    _get_app_state,
    _is_supervisor_request,
    _prepare_chat,
    _prepare_chat_fast,
    _save_extracted_memories,
    decrement_active,
    increment_active,
    logger,
)
from src.observability.logging import RequestContext, request_context
from src.observability.request_log_models import RequestLogEntry
from src.observability.trace_logger import RequestTrace

router = APIRouter()


async def _run_supervisor_chat(
    req: ChatRequest, state, user_ctx: UserContext,
    request: Optional[Request] = None,
) -> AgentResponse:
    """Supervisor 엔트리 분기(비스트리밍) — Task 002, P0-2.

    `_prepare_chat`/`state.agent.execute`를 거치지 않고 `state.supervisor.supervise()`로
    decompose→위임→synthesize를 수행한 뒤, 기존 `/chat` 응답 포맷(AgentResponse)으로 반환한다.
    메인(엔트리)이 컨텍스트/세션 turn 기록을 소유한다(§6-5).

    주의: `AgentResponse.trace`는 `TraceInfo`만 허용하므로(pydantic), 레이턴시 집계용
    `RequestTrace`는 응답에 넣지 않는다. 다만 `supervise(trace=...)`로 전달해
    위임 서브 실행의 레이어별 처리시간을 기록하고 request_log의
    latency_breakdown으로만 영속화한다.
    """
    request_id = str(uuid.uuid4())
    session_id = req.session_id or str(uuid.uuid4())
    response_id = str(uuid.uuid4())
    supervisor_id = state.settings.supervisor_profile_id
    trace = RequestTrace(request_id=request_id)

    ctx_token = request_context.set(RequestContext(
        request_id=request_id,
        session_id=session_id,
        profile_id=supervisor_id,
        user_id=user_ctx.user_id,
    ))
    # 관측성: supervisor 경로도 레거시 경로와 동일하게 request_log에 남긴다.
    request_log_svc = getattr(state, "request_log_service", None)
    timer_start = time.monotonic()
    log_status_code = 200
    log_error_code: Optional[str] = None
    log_response_preview: Optional[str] = None
    try:
        tenant_id = user_ctx.tenant_id or state.settings.default_tenant_id
        await state.session_memory.create_session(
            session_id=session_id,
            profile_id=supervisor_id,
            user_id=user_ctx.user_id,
            tenant_id=tenant_id,
        )

        # 호출자가 history를 주면 신뢰원천으로 사용(기존 _prepare_chat과 동일 관용구).
        if req.history:
            history = [
                {"role": h.get("role"), "content": h.get("content")}
                for h in req.history
                if h.get("role") and h.get("content")
            ]
        else:
            history = await state.session_memory.get_turns(session_id)

        agent_ctx = AgentContext(
            session_id=session_id,
            user_id=user_ctx.user_id,
            user_role=user_ctx.user_role,
            conversation_history=history,
            metadata=req.metadata or {},
            tenant_id=tenant_id,
        )

        response = await state.supervisor.supervise(
            req.question, agent_ctx, user_ctx, trace=trace,
        )

        await state.session_memory.add_turn(session_id, "user", req.question)
        await state.session_memory.add_turn(session_id, "assistant", response.answer)

        trace.log_summary()
        response.response_id = response_id
        log_response_preview = RequestLogEntry.truncate_preview(response.answer)
        return response
    except Exception:
        log_status_code = 500
        log_error_code = "supervisor_error"
        raise
    finally:
        if request_log_svc is not None:
            safe_enqueue(
                request_log_svc,
                RequestLogEntry(
                    api_key_id=getattr(user_ctx, "api_key_id", None),
                    profile_id=supervisor_id,
                    status_code=log_status_code,
                    latency_ms=int((time.monotonic() - timer_start) * 1000),
                    error_code=log_error_code,
                    request_preview=RequestLogEntry.truncate_preview(req.question),
                    response_preview=log_response_preview,
                    response_id=response_id,
                    client_ip=(request.client.host if request and request.client else None),
                    user_id=getattr(user_ctx, "user_id", None),
                    latency_breakdown=trace.summary(),
                ),
            )
        request_context.reset(ctx_token)


async def _run_supervisor_chat_stream(
    req: ChatRequest, state, user_ctx: UserContext,
    request: Optional[Request] = None,
):
    """Supervisor 엔트리 분기(스트리밍) — Task 002, P0-2 / 토큰 스트리밍.

    `supervise_stream()`이 최종 답변 생성 토큰(단일 위임 passthrough=서브 토큰,
    다중 위임=synthesize 토큰)을 실시간으로 흘린다. 토큰이 하나도 안 나간 경로
    (워크플로우 핸드오프/폴백 등, done.streamed=False)는 기존처럼 answer를
    단일 token으로 방출해 화면 공백을 막는다.
    """
    request_id = str(uuid.uuid4())
    session_id = req.session_id or str(uuid.uuid4())
    response_id = str(uuid.uuid4())
    supervisor_id = state.settings.supervisor_profile_id
    trace = RequestTrace(request_id=request_id)

    ctx_token = request_context.set(RequestContext(
        request_id=request_id,
        session_id=session_id,
        profile_id=supervisor_id,
        user_id=user_ctx.user_id,
    ))

    # 관측성: supervisor 스트리밍 경로도 request_log에 남긴다 (기존 공백).
    request_log_svc = getattr(state, "request_log_service", None)
    timer_start = time.monotonic()

    async def event_generator():
        log_status_code = 200
        log_error_code: Optional[str] = None
        log_response_preview: Optional[str] = None
        try:
            # 즉시 진행 이벤트 방출 — supervisor 경로는 첫 토큰까지 수십 초간
            # 무신호라 화면이 죽은 것처럼 보인다. 연결 생존 신호를 먼저 보낸다.
            yield {"event": "trace", "data": json.dumps(
                {"step": "supervisor", "status": "start"}, ensure_ascii=False,
            )}

            tenant_id = user_ctx.tenant_id or state.settings.default_tenant_id
            await state.session_memory.create_session(
                session_id=session_id,
                profile_id=supervisor_id,
                user_id=user_ctx.user_id,
                tenant_id=tenant_id,
            )

            if req.history:
                history = [
                    {"role": h.get("role"), "content": h.get("content")}
                    for h in req.history
                    if h.get("role") and h.get("content")
                ]
            else:
                history = await state.session_memory.get_turns(session_id)

            agent_ctx = AgentContext(
                session_id=session_id,
                user_id=user_ctx.user_id,
                user_role=user_ctx.user_role,
                conversation_history=history,
                metadata=req.metadata or {},
                tenant_id=tenant_id,
            )

            response = None
            streamed = False
            async for event in state.supervisor.supervise_stream(
                req.question, agent_ctx, user_ctx, trace=trace,
            ):
                event_type = event["type"]
                if event_type == "token":
                    yield {"event": "token", "data": json.dumps({"delta": event["data"]}, ensure_ascii=False)}
                elif event_type == "replace":
                    yield {"event": "replace", "data": json.dumps({"delta": event["data"]}, ensure_ascii=False)}
                elif event_type == "done":
                    response = event["data"]["response"]
                    streamed = event["data"]["streamed"]

            if response is None:
                # supervise_stream은 항상 done을 내지만, 방어적으로 빈 응답을 막는다.
                raise RuntimeError("supervisor stream ended without done event")

            await state.session_memory.add_turn(session_id, "user", req.question)
            await state.session_memory.add_turn(session_id, "assistant", response.answer)

            if not streamed:
                # 버퍼드 경로(핸드오프/폴백/안내) — 화면 공백 방지 단일 방출.
                yield {"event": "token", "data": json.dumps({"delta": response.answer}, ensure_ascii=False)}
            yield {"event": "done", "data": json.dumps({
                "answer": response.answer,
                "profile_id": supervisor_id,
                # Phase 3: chatbot_id 미지정 요청이 supervisor로 흡수된 경우
                # 자동 라우팅으로 표기(레거시 오케스트레이터 응답과 동일 의미).
                "orchestrated": req.chatbot_id is None,
                "response_id": response_id,
                "confidence": None,
                "traversal_path": [],
                "sources": [s.model_dump() if hasattr(s, "model_dump") else s for s in response.sources],
            }, ensure_ascii=False)}

            trace.log_summary()
            log_response_preview = RequestLogEntry.truncate_preview(response.answer)
        except Exception as stream_err:
            log_status_code = 500
            log_error_code = "supervisor_stream_error"
            logger.error("supervisor_stream_error", error=str(stream_err), exc_info=True)
            raise
        finally:
            if request_log_svc is not None:
                safe_enqueue(
                    request_log_svc,
                    RequestLogEntry(
                        api_key_id=getattr(user_ctx, "api_key_id", None),
                        profile_id=supervisor_id,
                        status_code=log_status_code,
                        latency_ms=int((time.monotonic() - timer_start) * 1000),
                        error_code=log_error_code,
                        request_preview=RequestLogEntry.truncate_preview(req.question),
                        response_preview=log_response_preview,
                        response_id=response_id,
                        client_ip=(request.client.host if request and request.client else None),
                        user_id=getattr(user_ctx, "user_id", None),
                        latency_breakdown=trace.summary(),
                    ),
                )
            try:
                request_context.reset(ctx_token)
            except ValueError:
                pass  # 다른 Context에서 생성된 토큰(SSE 제너레이터는 별도 Task에서 실행)
            decrement_active()

    return EventSourceResponse(event_generator())


@router.post("/chat", response_model=AgentResponse)
async def chat(req: ChatRequest, request: Request):
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx, sub_key=req.session_id)

    if _is_supervisor_request(req.chatbot_id, state):
        # Supervisor 엔트리 분기(task-002, §0-2) — 이하 직접 모드/오케스트레이터 경로는 타지 않는다.
        increment_active()
        try:
            return await _run_supervisor_chat(req, state, user_ctx, request)
        finally:
            decrement_active()

    setup: Optional[_ChatSetup] = None

    increment_active()

    request_log_svc = getattr(state, "request_log_service", None)
    cache_svc = getattr(state, "response_cache_service", None)

    # Task 014: 응답 식별자 생성 (api 레이어가 단일 출처)
    response_id = str(uuid.uuid4())
    captured_faithfulness_score: Optional[float] = None

    status_code = 200
    error_code: Optional[str] = None
    cache_hit = False
    response_preview: Optional[str] = None
    profile_for_log: str = ""

    chat_timer_start = time.monotonic()
    try:
        setup = await _prepare_chat(req, request, user_ctx)
        profile_for_log = setup.profile_id or ""

        profile = await state.profile_store.get(setup.profile_id) if setup.profile_id else None
        plan_mode = getattr(setup.plan, "mode", None)
        mode_str = plan_mode.value if hasattr(plan_mode, "value") else str(plan_mode or "")
        cacheable = bool(profile) and should_use_cache(profile, mode_str, cache_svc)

        if cacheable:
            cached_text = await try_cache_get(
                cache_svc, setup.profile_id, mode_str, req.question,
                tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
            )
            if cached_text is not None:
                cache_hit = True
                response_preview = RequestLogEntry.truncate_preview(cached_text)
                await state.session_memory.add_turn(setup.session_id, "user", req.question)
                await state.session_memory.add_turn(setup.session_id, "assistant", cached_text)
                # Task 014: 캐시 응답도 response_id 포함
                return AgentResponse(answer=cached_text, response_id=response_id)

        response = await state.agent.execute(
            question=req.question,
            plan=setup.plan,
            session_id=setup.session_id,
            trace=setup.trace,
            context=setup.context,
        )

        await state.session_memory.add_turn(setup.session_id, "user", req.question)
        await state.session_memory.add_turn(setup.session_id, "assistant", response.answer)

        if profile and profile.memory_type in ("session", "long"):
            asyncio.create_task(_save_extracted_memories(
                state=state,
                tenant_id=user_ctx.user_id,
                turns=[
                    {"role": "user", "content": req.question},
                    {"role": "assistant", "content": response.answer},
                ],
                retention_days=profile.memory_retention_days,
            ))

        setup.trace.log_summary()

        if response.trace:
            response.trace.request_id = setup.trace.request_id
            response.trace.latency_ms = setup.trace.total_ms

        response_preview = RequestLogEntry.truncate_preview(response.answer)
        # Task 014: finally 에서 request_log 에 기록할 점수 캡처
        captured_faithfulness_score = response.guardrail_score

        if cacheable and response.answer:
            await try_cache_put(
                cache_svc, setup.profile_id, mode_str, req.question, response.answer,
                tenant_id=user_ctx.tenant_id or state.settings.default_tenant_id,
            )

        # Task 014: 응답에 response_id 주입 (JSON body)
        response.response_id = response_id
        # guardrail_score 는 내부 전달용 — 클라이언트 응답에서 제거
        response.guardrail_score = None
        return response

    except HTTPException as he:
        status_code = he.status_code
        error_code = f"http_{he.status_code}"
        raise
    except Exception as e:
        status_code = 500
        error_code = "internal_error"
        logger.error("chat_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if request_log_svc is not None:
            safe_enqueue(
                request_log_svc,
                RequestLogEntry(
                    api_key_id=getattr(user_ctx, "api_key_id", None),
                    profile_id=profile_for_log or None,
                    status_code=status_code,
                    latency_ms=int((time.monotonic() - chat_timer_start) * 1000),
                    cache_hit=cache_hit,
                    error_code=error_code,
                    request_preview=RequestLogEntry.truncate_preview(req.question),
                    response_preview=response_preview,
                    # Task 014: 응답 식별자 + faithfulness 스코어 영속화
                    response_id=response_id,
                    faithfulness_score=captured_faithfulness_score,
                    # Phase 3: 관측성 — IP·user_id·레이어별 처리시간
                    client_ip=(request.client.host if request.client else None),
                    user_id=getattr(user_ctx, "user_id", None),
                    latency_breakdown=(setup.trace.summary() if setup and setup.trace else None),
                ),
            )
        if setup:
            request_context.reset(setup.ctx_token)
        decrement_active()


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx, sub_key=req.session_id)

    if _is_supervisor_request(req.chatbot_id, state):
        # Supervisor 엔트리 분기(task-002, §0-2) — 이하 직접 모드/오케스트레이터 경로는 타지 않는다.
        # increment는 여기서, decrement는 제너레이터 종료 시점(finally)에서 수행한다
        # (기존 /chat/stream과 동일하게 활성 카운트가 스트림 전체 수명을 감싸도록).
        increment_active()
        return await _run_supervisor_chat_stream(req, state, user_ctx, request)

    increment_active()

    request_log_svc = getattr(state, "request_log_service", None)
    stream_timer_start = time.monotonic()
    # Task 014: 응답 식별자 (api 단일 출처). SSE done 이벤트 + request_log 에 기록.
    response_id = str(uuid.uuid4())

    try:
        setup = await _prepare_chat_fast(req, request, user_ctx)
    except HTTPException as he:
        if request_log_svc is not None:
            elapsed_ms = int((time.monotonic() - stream_timer_start) * 1000)
            safe_enqueue(
                request_log_svc,
                RequestLogEntry(
                    api_key_id=getattr(user_ctx, "api_key_id", None),
                    status_code=he.status_code,
                    latency_ms=elapsed_ms,
                    error_code=f"http_{he.status_code}",
                    request_preview=RequestLogEntry.truncate_preview(req.question),
                    response_id=response_id,
                    client_ip=(request.client.host if request.client else None),
                    user_id=getattr(user_ctx, "user_id", None),
                ),
            )
        decrement_active()
        raise
    except Exception as e:
        logger.error("chat_stream_setup_error", error=str(e), exc_info=True)
        if request_log_svc is not None:
            elapsed_ms = int((time.monotonic() - stream_timer_start) * 1000)
            safe_enqueue(
                request_log_svc,
                RequestLogEntry(
                    api_key_id=getattr(user_ctx, "api_key_id", None),
                    status_code=500,
                    latency_ms=elapsed_ms,
                    error_code="internal_error",
                    request_preview=RequestLogEntry.truncate_preview(req.question),
                    response_id=response_id,
                    client_ip=(request.client.host if request.client else None),
                    user_id=getattr(user_ctx, "user_id", None),
                ),
            )
        decrement_active()
        raise HTTPException(status_code=500, detail="Internal server error")

    # 메모리 추출 대상 프로필 조회
    stream_profile = await state.profile_store.get(setup.profile_id) if setup.profile_id else None

    # 응답 캐시 세팅 (C: 스트리밍 경로 캐시 연결).
    # 비스트리밍 /chat 과 동일 키(tenant|profile|mode|normalized) 로 조회/저장한다.
    cache_svc = getattr(state, "response_cache_service", None)
    plan_mode = getattr(setup.plan, "mode", None)
    mode_str = plan_mode.value if hasattr(plan_mode, "value") else str(plan_mode or "")
    stream_tenant_id = user_ctx.tenant_id or state.settings.default_tenant_id
    # per-turn override(directive/외부 context)가 있으면 질문 키만으로는 답이 달라져 캐시 부적합.
    has_per_turn_override = bool(
        (req.directive and req.directive.strip()) or (req.context and req.context.strip())
    )
    cacheable = (
        bool(stream_profile)
        and not has_per_turn_override
        and should_use_cache(stream_profile, mode_str, cache_svc)
    )
    cached_answer: Optional[str] = None
    if cacheable:
        cached_answer = await try_cache_get(
            cache_svc, setup.profile_id, mode_str, req.question,
            tenant_id=stream_tenant_id,
        )

    # context reset을 generator 종료 시점으로 연기
    async def event_generator():
        gen_status_code = 200
        gen_error_code: Optional[str] = None
        gen_response_preview: Optional[str] = None
        gen_cache_hit = False
        # Task 014: done 이벤트에서 faithfulness_score 포집 (finally enqueue 에 사용)
        captured_faithfulness_score: Optional[float] = None
        try:
            # 캐시 히트: 저장된 답변을 즉시 재생하고 full 파이프라인을 생략한다.
            # token 1건(전문) + done 으로 흘려 프론트 렌더링을 기존 스트리밍과 동일하게 유지.
            if cached_answer is not None:
                gen_cache_hit = True
                yield {"event": "token", "data": json.dumps({"delta": cached_answer}, ensure_ascii=False)}
                yield {"event": "done", "data": json.dumps({
                    "answer": cached_answer,
                    "profile_id": setup.profile_id,
                    "orchestrated": setup.orchestrated,
                    "response_id": response_id,
                    "confidence": None,
                    "traversal_path": [],
                    "cached": True,
                }, ensure_ascii=False)}
                await state.session_memory.add_turn(setup.session_id, "user", req.question)
                await state.session_memory.add_turn(setup.session_id, "assistant", cached_answer)
                gen_response_preview = RequestLogEntry.truncate_preview(cached_answer)
                return

            answer_parts = []
            async for event in state.agent.execute_stream(
                question=req.question, plan=setup.plan,
                session_id=setup.session_id, trace=setup.trace,
                context=setup.context,
            ):
                event_type = event["type"]
                if event_type == "thinking":
                    yield {"event": "trace", "data": json.dumps({"step": "thinking", "content": event["data"]}, ensure_ascii=False)}
                elif event_type == "token":
                    answer_parts.append(event["data"])
                    yield {"event": "token", "data": json.dumps({"delta": event["data"]}, ensure_ascii=False)}
                elif event_type == "replace":
                    answer_parts.clear()
                    answer_parts.append(event["data"])
                    yield {"event": "replace", "data": json.dumps({"delta": event["data"]}, ensure_ascii=False)}
                elif event_type == "trace":
                    yield {"event": "trace", "data": json.dumps(event["data"], ensure_ascii=False)}
                elif event_type == "done":
                    done_data = event["data"]
                    done_data["profile_id"] = setup.profile_id
                    done_data["orchestrated"] = setup.orchestrated
                    # KMS 프론트 호환: answer, confidence, traversal_path 필드 추가
                    done_data.setdefault("answer", "".join(answer_parts))
                    # 빈 응답 폴백 — 토큰이 하나도 없거나 공백뿐이면 화면이 빈 채로 끝난다.
                    # 폴백 발화로 치환하고 token 으로도 흘려 스트리밍 화면을 채운다.
                    # 문구는 프로필 설정(empty_response_fallback)에서 가져온다(서비스별 톤).
                    # 특정 서비스 문구를 공용 게이트에 하드코딩하지 않는다.
                    if not (done_data.get("answer") or "").strip():
                        fallback = (
                            (stream_profile and stream_profile.empty_response_fallback)
                            or "죄송해요, 방금 응답을 만들지 못했어요. 다시 한 번 말씀해 주시겠어요?"
                        )
                        done_data["answer"] = fallback
                        answer_parts.clear()
                        answer_parts.append(fallback)
                        yield {"event": "token", "data": json.dumps({"delta": fallback}, ensure_ascii=False)}
                    done_data.setdefault("confidence", None)
                    done_data.setdefault("traversal_path", [])
                    # Task 014: response_id 주입 + faithfulness_score 캡처
                    done_data["response_id"] = response_id
                    score_value = done_data.get("faithfulness_score")
                    if isinstance(score_value, (int, float)):
                        captured_faithfulness_score = float(score_value)
                    yield {"event": "done", "data": json.dumps(done_data, ensure_ascii=False)}

            # C: 스트림이 정상 종료됐는데 토큰이 하나도 없는 경우(예: LLM 생성이 done 을
            # 방출하지 못하고 빈 결과로 끝남) 위 done-핸들러 폴백이 걸리지 않아 빈 말풍선이
            # 남는다. 여기서 최종 방어로 폴백을 발화한다(token + done). done 이벤트가 이미
            # 나왔다면 answer_parts 가 비어있지 않으므로 이 블록은 건너뛴다.
            used_empty_fallback = False
            if not "".join(answer_parts).strip():
                used_empty_fallback = True
                fallback = (
                    (stream_profile and stream_profile.empty_response_fallback)
                    or "죄송해요, 방금 응답을 만들지 못했어요. 다시 한 번 말씀해 주시겠어요?"
                )
                answer_parts.clear()
                answer_parts.append(fallback)
                yield {"event": "token", "data": json.dumps({"delta": fallback}, ensure_ascii=False)}
                yield {"event": "done", "data": json.dumps({
                    "answer": fallback,
                    "profile_id": setup.profile_id,
                    "orchestrated": setup.orchestrated,
                    "response_id": response_id,
                    "confidence": None,
                    "traversal_path": [],
                    "empty_fallback": True,
                }, ensure_ascii=False)}
                logger.warning(
                    "stream_empty_fallback",
                    request_id=setup.trace.request_id,
                    profile_id=setup.profile_id,
                )

            full_answer = "".join(answer_parts)
            await state.session_memory.add_turn(setup.session_id, "user", req.question)
            await state.session_memory.add_turn(setup.session_id, "assistant", full_answer)

            if stream_profile and stream_profile.memory_type in ("session", "long"):
                asyncio.create_task(_save_extracted_memories(
                    state=state,
                    tenant_id=user_ctx.user_id,
                    turns=[
                        {"role": "user", "content": req.question},
                        {"role": "assistant", "content": full_answer},
                    ],
                    retention_days=stream_profile.memory_retention_days,
                ))

            setup.trace.log_summary()
            logger.info(
                "stream_complete",
                answer_len=len(full_answer),
                total_ms=round(setup.trace.total_ms, 1),
            )
            gen_response_preview = RequestLogEntry.truncate_preview(full_answer)

            # 스트림 완료 후 응답 캐시 저장 (miss 였던 경우만; 빈 답변·폴백 제외).
            # 폴백은 일시적 생성 실패의 플레이스홀더라 캐시하면 1시간 오답이 고정된다.
            if cacheable and full_answer.strip() and not used_empty_fallback:
                await try_cache_put(
                    cache_svc, setup.profile_id, mode_str, req.question, full_answer,
                    tenant_id=stream_tenant_id,
                )
        except Exception as stream_err:
            gen_status_code = 500
            gen_error_code = "stream_error"
            logger.error("chat_stream_error", error=str(stream_err), exc_info=True)
            raise
        finally:
            # Request log enqueue — generator 종료 후 (R1 보장)
            if request_log_svc is not None:
                elapsed_ms = int((time.monotonic() - stream_timer_start) * 1000)
                safe_enqueue(
                    request_log_svc,
                    RequestLogEntry(
                        api_key_id=getattr(user_ctx, "api_key_id", None),
                        profile_id=setup.profile_id or None,
                        status_code=gen_status_code,
                        latency_ms=elapsed_ms,
                        cache_hit=gen_cache_hit,
                        error_code=gen_error_code,
                        request_preview=RequestLogEntry.truncate_preview(req.question),
                        response_preview=gen_response_preview,
                        # Task 014: 응답 식별자 + faithfulness 스코어 영속화
                        response_id=response_id,
                        faithfulness_score=captured_faithfulness_score,
                        # Phase 3: 관측성 — IP·user_id·레이어별 처리시간(trace)
                        client_ip=(request.client.host if request.client else None),
                        user_id=getattr(user_ctx, "user_id", None),
                        latency_breakdown=(setup.trace.summary() if setup and setup.trace else None),
                    ),
                )
            # SSE 제너레이터는 별도 Task에서 실행되므로
            # ContextVar 토큰 reset은 안전하게 스킵
            try:
                request_context.reset(setup.ctx_token)
            except ValueError:
                pass  # 다른 Context에서 생성된 토큰
            decrement_active()

    return EventSourceResponse(event_generator())
