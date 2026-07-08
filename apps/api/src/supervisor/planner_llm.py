"""Supervisor decompose/synthesize LLM 계획기 (P0-3).

메인(Supervisor)의 오케스트레이션 전용 경량 LLM(`orchestration_llm`, 4B급)을 사용해
질의를 서브쿼리로 분해(decompose)하고, 서브 실행 결과를 종합(synthesize)한다.

주의: 이 모듈은 `main_llm`(대형 생성 전용)을 쓰지 않는다 — decompose/synthesize는
지연시간이 중요한 오케스트레이션 작업이라 경량 모델을 쓴다(§ 지연 관리, 앵커:
`agent/planner.py`의 `generate_json` 관용구를 재사용).
"""

from __future__ import annotations

from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.supervisor.models import DelegationPlan, DelegationStep, SubAgentResult

logger = get_logger(__name__)

DECOMPOSE_SYSTEM_PROMPT = """당신은 여러 전문 AI 에이전트를 조율하는 메인 슈퍼바이저입니다.
사용자 질문을 분석해, 아래 후보 에이전트 중 필요한 만큼 선택하여 서브 질의로 분해하세요.

규칙:
- 각 서브 질의는 후보 에이전트 목록에 있는 id만 담당자로 지정할 수 있습니다.
- 질문이 여러 도메인에 걸쳐 있으면 도메인별로 서브 질의를 나누세요.
- 단순 질문이고 후보가 1개뿐이면 위임 1건으로 충분합니다.
- 근거 없는 위임은 만들지 마세요."""

DECOMPOSE_USER_TEMPLATE = """후보 에이전트:
{candidates_desc}

질문: {question}

JSON 형식으로 응답하세요:
{{
  "delegations": [
    {{"profile": "candidate_id", "subquery": "서브에게 전달할 구체적 질의", "reason": "선정 근거"}}
  ]
}}"""

SYNTHESIZE_SYSTEM_PROMPT = """당신은 여러 전문 에이전트의 답변을 종합하는 메인 슈퍼바이저입니다.
아래 서브 에이전트들의 답변을 근거로 사용자 질문에 대한 하나의 종합 답변을 작성하세요.
서로 다른 도메인의 정보를 자연스럽게 통합하고, 출처를 임의로 지어내지 마세요."""

SYNTHESIZE_USER_TEMPLATE = """질문: {question}

서브 에이전트 답변:
{sub_answers}

위 답변들을 종합하여 하나의 답변을 작성하세요."""

FALLBACK_NO_RESULT = "죄송합니다. 요청을 처리할 수 있는 하위 에이전트로부터 유효한 답변을 받지 못했습니다."


class SupervisorPlanner:
    """decompose(질의 분해) / synthesize(결과 종합)를 경량 오케스트레이션 LLM으로 수행한다."""

    def __init__(self, orchestration_llm: LLMProvider) -> None:
        self._llm = orchestration_llm

    async def decompose(
        self,
        question: str,
        allowed: set[str] | None,
        candidate_profiles: list[dict],
    ) -> DelegationPlan:
        """질의를 서브쿼리로 분해하고 담당 프로파일을 지정한다.

        candidate_profiles: allowed에 속한 프로파일의 {id, name, description}만 전달받는다
        (스코프 밖 프로파일은 프롬프트에 노출하지 않는다 — 관문과 정합).
        파싱 실패/빈 계획이면 단일 위임 폴백으로 degrade한다(예외 전파 금지).
        """
        if not candidate_profiles:
            logger.warning("supervisor_decompose_no_candidates")
            return DelegationPlan(delegations=[], is_adaptive=False)

        candidates_desc = "\n".join(
            f"- {c.get('id', '')}: {c.get('name', '')} — {c.get('description', '')}"
            for c in candidate_profiles
        )
        prompt = DECOMPOSE_USER_TEMPLATE.format(candidates_desc=candidates_desc, question=question)

        try:
            result = await self._llm.generate_json(prompt, system=DECOMPOSE_SYSTEM_PROMPT)
            delegations = self._parse_delegations(result, candidate_profiles)
        except Exception as e:  # noqa: BLE001 - decompose 실패는 폴백으로 degrade, 전파 금지
            logger.warning("supervisor_decompose_llm_error", error=str(e))
            delegations = []

        if not delegations:
            logger.info("supervisor_decompose_fallback_single", candidate=candidate_profiles[0].get("id"))
            fallback_id = candidate_profiles[0].get("id", "")
            delegations = [
                DelegationStep(profile=fallback_id, subquery=question, reason="decompose 폴백(단일 위임)")
            ]

        return DelegationPlan(delegations=delegations, is_adaptive=False)

    def _parse_delegations(
        self, result: dict, candidate_profiles: list[dict]
    ) -> list[DelegationStep]:
        """LLM 원시 출력(dict)을 검증된 DelegationStep 목록으로 정제한다."""
        candidate_ids = {c.get("id") for c in candidate_profiles}
        raw_delegations = result.get("delegations", []) if isinstance(result, dict) else []

        steps: list[DelegationStep] = []
        for item in raw_delegations:
            if not isinstance(item, dict):
                continue
            profile = item.get("profile", "")
            subquery = item.get("subquery", "")
            if profile not in candidate_ids or not subquery:
                continue
            steps.append(
                DelegationStep(
                    profile=profile,
                    subquery=subquery,
                    reason=item.get("reason", ""),
                )
            )
        return steps

    async def synthesize(self, question: str, results: list[SubAgentResult]) -> str:
        """서브 실행 결과를 종합해 메인의 최종 답변을 생성한다.

        완료(ok=True)된 결과만 근거로 종합하고, 부분 결과만 있으면 그것으로
        종합하되 불완전함을 문장으로 표시한다(§8 degrade P0 정책). 결과가 0건이면
        안전한 폴백 문구를 반환한다(빈 응답 금지).
        """
        ok_results = [r for r in results if r.ok]
        failed_count = len(results) - len(ok_results)

        if not ok_results:
            if results:
                logger.warning("supervisor_synthesize_all_failed", count=len(results))
            return FALLBACK_NO_RESULT

        sub_answers = "\n\n".join(f"[{r.profile}]\n{r.answer}" for r in ok_results)
        prompt = SYNTHESIZE_USER_TEMPLATE.format(question=question, sub_answers=sub_answers)

        try:
            answer = await self._llm.generate(prompt, system=SYNTHESIZE_SYSTEM_PROMPT)
        except Exception as e:  # noqa: BLE001 - 종합 실패 시에도 결과를 안전하게 반환
            logger.warning("supervisor_synthesize_llm_error", error=str(e))
            answer = "\n\n".join(r.answer for r in ok_results)

        if failed_count > 0:
            answer += f"\n\n(참고: 일부 하위 에이전트({failed_count}건)의 응답을 받지 못해 일부 정보가 누락되었을 수 있습니다.)"

        return answer
