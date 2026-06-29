"""공유 모듈 헬퍼 — graph_executor 분할 산출물.

모드별 mixin 3개(workflow/deterministic/agentic)가 공통으로 사용하는
순수 함수들을 모아 순환 import 없이 재사용한다.
"""

from typing import Optional

from src.domain.execution_plan import ExecutionPlan


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
