"""Supervisor decompose/synthesize LLM 계획기 (P0-3).

투트랙 라이트사이징(모델 토폴로지 원칙)을 supervisor 내부에도 적용한다:
- decompose(구조적 JSON 분해) = 오케스트레이션 → 경량 `orchestration_llm`(4B급)
- synthesize(사용자 대면 최종 생성) = 생성 → 대형 `synthesize_llm`(main, 9B급)

실측 근거(2026-07-08): synthesize를 4B로 돌리자 중국어 혼입("보험赔付")과
부정문 뒤집힘("상담하는 것이 가장 부적절합니다") 등 사용자 대면 품질 사고 발생.
"생성은 큰 LLM" 원칙 위반이었음 — synthesize_llm 분리로 교정.
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
- **질문과 명확히 관련된 에이전트에만 위임하세요. 관련성이 불확실하면 그 에이전트는 제외하세요.**
  (예: 보험 사고 질문을 사주·SNS운영 에이전트에 위임하는 것은 오라우팅입니다)
- **대부분의 질문은 위임 1건으로 충분합니다.** 질문이 서로 다른 도메인의 정보를
  명시적으로 함께 요구할 때만 여러 건으로 나누세요.
- 근거 없는 위임은 만들지 마세요. 적을수록 좋습니다."""

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
서로 다른 도메인의 정보를 자연스럽게 통합하고, 출처를 임의로 지어내지 마세요.
반드시 한국어로만 작성하세요(한자·중국어·영어 단어 혼입 금지)."""

SYNTHESIZE_USER_TEMPLATE = """질문: {question}

서브 에이전트 답변:
{sub_answers}

위 답변들을 종합하여 하나의 답변을 작성하세요."""

FALLBACK_NO_RESULT = "죄송합니다. 요청을 처리할 수 있는 하위 에이전트로부터 유효한 답변을 받지 못했습니다."

REPLAN_SYSTEM_PROMPT = """당신은 여러 전문 AI 에이전트를 조율하는 메인 슈퍼바이저입니다.
1차 위임 결과를 검토해, 사용자 질문에 답하기 위해 **추가 위임이 반드시 필요한지** 판단하세요.

규칙:
- **대부분의 경우 추가 위임은 불필요합니다. 확신이 없으면 빈 배열을 반환하세요.**
- 이미 받은 답변으로 질문에 답할 수 있으면 빈 배열을 반환하세요.
- 추가 위임은 "질문이 명시적으로 요구했는데 아직 어떤 답변도 다루지 않은 도메인"이 있을 때만 만드세요.
- 이미 위임했던 에이전트에 같은 질의를 반복하지 마세요.
- 후보 에이전트 목록에 있는 id만 담당자로 지정할 수 있습니다."""

REPLAN_USER_TEMPLATE = """후보 에이전트:
{candidates_desc}

질문: {question}

1차 위임 결과:
{sub_answers}

추가 위임이 필요하면 JSON으로, 불필요하면 빈 배열로 응답하세요:
{{
  "delegations": [
    {{"profile": "candidate_id", "subquery": "서브에게 전달할 구체적 질의", "reason": "추가 위임 근거"}}
  ]
}}"""

REVIEW_SYSTEM_PROMPT = """당신은 하위 에이전트의 답변을 검토하는 메인 슈퍼바이저입니다.
답변이 위임한 서브 질의에 실제로 응답했는지만 판정하세요(재작성 금지).

판정 기준(이것만 본다 — 안전성/문체는 서브 가드레일 소관이므로 중복 검사 금지):
- 답변이 서브 질의와 관련이 있는가?
- 답변이 근거 없이 질의를 회피하거나 완전히 다른 주제를 다루지 않는가?
확신이 없으면 passed=true로 판정하세요(과차단 금지)."""

REVIEW_USER_TEMPLATE = """서브 질의: {subquery}

서브 답변:
{answer}

JSON 형식으로 판정하세요:
{{"passed": true/false, "note": "판정 근거 한 줄"}}"""


class SupervisorPlanner:
    """decompose(질의 분해)=경량 LLM / synthesize(사용자 대면 종합)=대형 LLM."""

    def __init__(
        self,
        orchestration_llm: LLMProvider,
        synthesize_llm: LLMProvider | None = None,
    ) -> None:
        self._llm = orchestration_llm
        # 사용자 대면 최종 생성은 대형 모델("생성은 큰 LLM"). 미주입 시 폴백.
        self._synth_llm = synthesize_llm or orchestration_llm

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

        candidates_desc = self._format_candidates(candidate_profiles)
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

    @staticmethod
    def _format_candidates(candidate_profiles: list[dict]) -> str:
        """후보 프로파일을 decompose 프롬프트용으로 렌더한다.

        description 외에 담당 도메인·인텐트를 함께 노출한다 — 경량 로컬 LLM 이
        id/name 만으로 추측하지 않도록 구별 신호를 최대한 준다(오라우팅 실사고 대응).
        """
        lines = []
        for c in candidate_profiles:
            line = f"- {c.get('id', '')}: {c.get('name', '')} — {c.get('description', '')}"
            extras = []
            if c.get("domains"):
                extras.append(f"담당 도메인: {', '.join(c['domains'])}")
            if c.get("intents"):
                extras.append(f"담당 작업: {', '.join(c['intents'])}")
            if extras:
                line += f" ({'; '.join(extras)})"
            lines.append(line)
        return "\n".join(lines)

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

    async def replan(
        self,
        question: str,
        results: list[SubAgentResult],
        candidate_profiles: list[dict],
    ) -> DelegationPlan:
        """1차 위임 결과를 검토해 추가 위임 계획을 만든다 (P1-1 adaptive replan).

        decompose와 달리 **빈 계획이 정상 종료**다(추가 위임 불필요) — 폴백 위임을
        만들지 않는다. LLM 실패 시에도 빈 계획으로 degrade한다(전파 금지).
        """
        if not candidate_profiles:
            return DelegationPlan(delegations=[], is_adaptive=False)

        ok_results = [r for r in results if r.ok]
        if not ok_results:
            # 전부 실패한 라운드에서 재위임하면 같은 실패를 반복할 뿐이다 — 종합으로 넘긴다.
            return DelegationPlan(delegations=[], is_adaptive=False)

        candidates_desc = self._format_candidates(candidate_profiles)
        sub_answers = "\n\n".join(f"[{r.profile}]\n{r.answer}" for r in ok_results)
        prompt = REPLAN_USER_TEMPLATE.format(
            candidates_desc=candidates_desc, question=question, sub_answers=sub_answers
        )

        try:
            result = await self._llm.generate_json(prompt, system=REPLAN_SYSTEM_PROMPT)
            delegations = self._parse_delegations(result, candidate_profiles)
        except Exception as e:  # noqa: BLE001 - replan 실패는 빈 계획으로 degrade, 전파 금지
            logger.warning("supervisor_replan_llm_error", error=str(e))
            delegations = []

        # 이미 위임한 프로파일 재위임 금지(같은 질의 반복 방지 — 프롬프트 규칙의 코드 강제).
        delegated = {r.profile for r in results}
        delegations = [d for d in delegations if d.profile not in delegated]

        if delegations:
            logger.info(
                "supervisor_replan_delegations",
                profiles=[d.profile for d in delegations],
            )
        return DelegationPlan(delegations=delegations, is_adaptive=False)

    async def review(self, subquery: str, result: SubAgentResult) -> dict:
        """서브 답변 1건을 판정한다 (P1-4 메인 검토 게이트).

        재생성이 아니라 판정(pass/fail + 근거 한 줄)만 한다. 판정 불능(LLM 실패,
        파싱 실패)은 fail-open(passed=True) — 게이트 장애가 답변 유실로 번지면 안 된다.
        """
        prompt = REVIEW_USER_TEMPLATE.format(subquery=subquery, answer=result.answer)
        try:
            verdict = await self._llm.generate_json(prompt, system=REVIEW_SYSTEM_PROMPT)
        except Exception as e:  # noqa: BLE001 - 판정 실패는 fail-open, 전파 금지
            logger.warning("supervisor_review_llm_error", profile=result.profile, error=str(e))
            return {"passed": True, "note": "판정 실패(fail-open)"}

        if not isinstance(verdict, dict) or "passed" not in verdict:
            return {"passed": True, "note": "판정 파싱 실패(fail-open)"}
        return {"passed": bool(verdict["passed"]), "note": str(verdict.get("note", ""))}

    @staticmethod
    def _split_ok(results: list[SubAgentResult]) -> tuple[list[SubAgentResult], int]:
        ok_results = [r for r in results if r.ok]
        return ok_results, len(results) - len(ok_results)

    @staticmethod
    def _build_synthesize_prompt(question: str, ok_results: list[SubAgentResult]) -> str:
        sub_answers = "\n\n".join(f"[{r.profile}]\n{r.answer}" for r in ok_results)
        return SYNTHESIZE_USER_TEMPLATE.format(question=question, sub_answers=sub_answers)

    @staticmethod
    def _partial_note(failed_count: int) -> str:
        return (
            f"\n\n(참고: 일부 하위 에이전트({failed_count}건)의 응답을 받지 못해 "
            f"일부 정보가 누락되었을 수 있습니다.)"
        )

    async def synthesize(self, question: str, results: list[SubAgentResult]) -> str:
        """서브 실행 결과를 종합해 메인의 최종 답변을 생성한다.

        완료(ok=True)된 결과만 근거로 종합하고, 부분 결과만 있으면 그것으로
        종합하되 불완전함을 문장으로 표시한다(§8 degrade P0 정책). 결과가 0건이면
        안전한 폴백 문구를 반환한다(빈 응답 금지).
        """
        ok_results, failed_count = self._split_ok(results)

        if not ok_results:
            if results:
                logger.warning("supervisor_synthesize_all_failed", count=len(results))
            return FALLBACK_NO_RESULT

        prompt = self._build_synthesize_prompt(question, ok_results)

        try:
            answer = await self._synth_llm.generate(prompt, system=SYNTHESIZE_SYSTEM_PROMPT)
        except Exception as e:  # noqa: BLE001 - 종합 실패 시에도 결과를 안전하게 반환
            logger.warning("supervisor_synthesize_llm_error", error=str(e))
            answer = "\n\n".join(r.answer for r in ok_results)

        if failed_count > 0:
            answer += self._partial_note(failed_count)

        return answer

    async def synthesize_stream(self, question: str, results: list[SubAgentResult]):
        """synthesize의 토큰 스트리밍 판 — 같은 프롬프트·같은 degrade 의미론.

        LLM 스트림이 도중에 실패하면 이미 나간 토큰은 되돌릴 수 없으므로,
        ok 답변 원문 이어붙임으로 degrade하고 로그를 남긴다(빈 응답 금지 우선).
        """
        ok_results, failed_count = self._split_ok(results)

        if not ok_results:
            if results:
                logger.warning("supervisor_synthesize_all_failed", count=len(results))
            yield FALLBACK_NO_RESULT
            return

        prompt = self._build_synthesize_prompt(question, ok_results)

        try:
            async for token in self._synth_llm.generate_stream(prompt, system=SYNTHESIZE_SYSTEM_PROMPT):
                yield token
        except Exception as e:  # noqa: BLE001 - 스트림 실패 시에도 결과를 안전하게 반환
            logger.warning("supervisor_synthesize_stream_error", error=str(e))
            yield "\n\n".join(r.answer for r in ok_results)

        if failed_count > 0:
            yield self._partial_note(failed_count)
