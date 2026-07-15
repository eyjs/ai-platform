"""공유 모듈 헬퍼 — graph_executor 분할 산출물.

모드별 mixin 3개(workflow/deterministic/agentic)가 공통으로 사용하는
순수 함수들을 모아 순환 import 없이 재사용한다.
"""

from typing import Optional

from src.domain.execution_plan import ExecutionPlan


def insufficient_context_refusal(plan, prompt_results: list) -> Optional[str]:
    """RAG가 필요한데 관련 컨텍스트가 하나도 없으면 정직 반려 메시지를 반환한다.

    무관 검색(리랭커 하한 미달로 빈 결과)에서 LLM이 파라메트릭 지식으로
    지어내는 것을 원천 차단한다 — 근거 없는 답 대신 "자료 없음"을 말한다.
    needs_rag=False(일반 대화 등)는 컨텍스트가 없어도 정상이므로 게이트하지 않는다.
    반려 불필요 시 None.
    """
    from src.locale.bundle import get_locale

    if getattr(plan.strategy, "needs_rag", False) and not prompt_results:
        return get_locale().message("insufficient_relevance")
    return None


def _content_to_text(content) -> str:
    """LangChain 메시지 content 를 평문 텍스트로 평탄화한다.

    `AIMessageChunk.content` 는 모델에 따라 `str` 또는 content-block
    리스트(`list[str | dict]`, 예: `[{"type": "text", "text": "..."}]`)로
    반환된다. 리스트가 그대로 토큰 스트림에 흘러가면
    - 프론트엔드에서 `[object Object]` 로 렌더되고
    - `answer += content` (str + list) 에서 TypeError 가 발생한다.
    여기서 항상 str 로 정규화하여 두 문제를 차단한다.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return "" if content is None else str(content)


def _extract_faithfulness_score(guardrail_results: dict) -> Optional[float]:
    """guardrail_results 에서 faithfulness guard 의 수치 스코어를 추출한다.

    Task 014: api_request_logs.faithfulness_score 저장용.
    None 이 기본 (측정 불가, 또는 guard 미실행).
    """
    if not guardrail_results:
        return None
    entry = guardrail_results.get("faithfulness")
    if isinstance(entry, dict):
        score = entry.get("score")
        if isinstance(score, (int, float)):
            return float(score)
    return None


def _build_agentic_user_turn(question: str, plan: "ExecutionPlan") -> str:
    """에이전틱 user 턴 봉투를 구성한다.

    volatile(날짜+directive)과 이전 대화 기록을 user 턴에 주입한다.
    캐시된 system prefix(페르소나+grounding) 뒤에 붙으므로 prefix 캐시를 깨지 않으면서
    매턴 최신 날짜/지시를 전달한다(컴파일 그래프엔 volatile 미포함 → byte-stable).
    """
    prefix_parts: list[str] = []
    if plan.volatile_system_prompt:
        prefix_parts.append(f"[지침]\n{plan.volatile_system_prompt}")
    if plan.conversation_context:
        prefix_parts.append(f"[이전 대화 기록]\n{plan.conversation_context}")
    if not prefix_parts:
        return question
    return "\n\n".join(prefix_parts + [f"[현재 질문]\n{question}"])


def is_no_answer(text: str) -> bool:
    """답변에 '정보 부재' 정형 문구(시스템 프롬프트 처방)가 포함되는지."""
    from src.locale.bundle import get_locale
    if not text:
        return False
    return any(m in text for m in get_locale().raw_patterns("no_answer_markers"))


# 무답변 "지배" 판정 경계 — 이보다 짧으면 실질 내용이 없는 답변으로 본다.
NO_ANSWER_DOMINANT_MAX_LENGTH = 350
# 문구가 이 위치 안에서 시작하면 답변의 본질이 "부재 선언"이다.
NO_ANSWER_DOMINANT_HEAD_CHARS = 100


def is_no_answer_dominant(text: str) -> bool:
    """무답변이 답변의 **본질**인지 판정 — 확장 재시도의 트리거.

    구조적 구분(실사고 교훈):
    - 검색 빈손/정원 컷 → 짧은 "확인 필요" 답변 → 재시도 유효
    - 장문 답변 말미의 관용적 얼버무림("자세한 건 확인 필요") → 답변은
      유효 — 재시도하면 멀쩡한 답변을 버리고 같은 모델로 재굴림(순수 낭비)

    판정: 문구 포함 AND (전체가 짧거나 OR 문구가 서두에 등장).
    """
    from src.locale.bundle import get_locale
    if not text:
        return False
    markers = get_locale().raw_patterns("no_answer_markers")
    positions = [text.find(m) for m in markers if m in text]
    if not positions:
        return False
    if len(text) <= NO_ANSWER_DOMINANT_MAX_LENGTH:
        return True
    return min(positions) < NO_ANSWER_DOMINANT_HEAD_CHARS


def widen_plan(plan, cap: int = 16):
    """검색 정원을 2배(상한 cap)로 넓힌 새 plan을 반환한다 (원본 불변)."""
    import dataclasses
    widened_strategy = dataclasses.replace(
        plan.strategy,
        max_vector_chunks=min(cap, max(1, plan.strategy.max_vector_chunks) * 2),
    )
    return dataclasses.replace(plan, strategy=widened_strategy)


# 가드레일 판정 기반 재생성 임계 — 이 미만이면 "내용이 틀렸다"는 판정으로 본다
# (연산 왜곡 0.2, deep_eval 근거실패 0.3 해당. 경미한 warn 0.5는 재생성 안 함).
REGENERATE_SCORE_THRESHOLD = 0.35


def _collect_guardrail_warnings(results: dict) -> str:
    """가드레일 결과에서 warn 사유를 재생성 피드백용 문장으로 모은다."""
    warnings = []
    for name, v in results.items():
        if name.startswith("_") or not isinstance(v, dict):
            continue
        if v.get("action") == "warn":
            reason = v.get("reason") or v.get("message") or name
            warnings.append(f"{name}: {reason}")
    return "; ".join(warnings) if warnings else "품질 미달"
