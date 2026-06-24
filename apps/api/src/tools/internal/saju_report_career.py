"""사주 천직(Career) 리포트 생성 Tool.

saju_report_paper.py 패턴을 복제해 천직 5섹션(careerVessel, careerTalent,
careerDirection, careerTiming, careerStrategy) 리포트를 순차 생성한다.

도메인 분리 원칙(feedback-aiplatform-layering-domain-leak)에 따라
_extract_json·_collect_prior_summaries를 이 파일에 복제한다(공유 util 승격 금지).
"""

from __future__ import annotations

import json
from typing import Any

from src.domain.agent_context import AgentContext
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.tools.base import ToolResult
from src.tools.internal.saju_career_prompts import (
    CAREER_V2_SECTION_KEYS,
    get_career_section_prompt,
)
from src.tools.internal.saju_context_formatter import format_single_person_context

logger = get_logger(__name__)

_CAREER_REQUIRED_SECTIONS = frozenset(CAREER_V2_SECTION_KEYS)


def _extract_json(raw: str) -> dict:
    """LLM 응답에서 JSON 객체를 추출한다.

    마크다운 코드블록 제거 + 중괄호 범위 추출.
    """
    clean = raw.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()

    start = clean.find("{")
    end = clean.rfind("}")
    if start != -1 and end != -1:
        clean = clean[start : end + 1]

    return json.loads(clean)


class SajuReportCareerTool:
    """report.v2/career - 5섹션 순차 생성 Tool.

    Tool Protocol 준수: name, description, input_schema, execute().
    """

    name = "saju_report_career"
    description = "사주 천직 성공 리포트를 report.v2 JSON 형태로 생성합니다."
    input_schema: dict = {
        "type": "object",
        "properties": {
            "saju_data": {
                "type": "object",
                "description": "사주 원천 데이터 (basic, premium 포함)",
            },
        },
        "required": ["saju_data"],
    }

    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm = llm_provider

    async def execute(
        self,
        params: dict,
        context: AgentContext,
    ) -> ToolResult:
        """5섹션 천직 리포트를 순차 생성한다.

        params:
            saju_data: 사주 원천 데이터 dict

        Returns:
            ToolResult with report.v2/career JSON
        """
        saju_data = params.get("saju_data")
        if not saju_data:
            return ToolResult.fail("saju_data가 필요합니다.")

        context_str = format_single_person_context(saju_data, "사용자")

        # 챗 맥락 주입(killing point) — 이 사람이 묘묘와 나눈 질답(실제 고민·관심사)을
        # 리포트에 녹여 초개인화한다. 데이터만 보던 풀이를 "너 아까 ~ 했지" 수준으로.
        chat_ctx = saju_data.get("_chatContext") if isinstance(saju_data, dict) else None
        if chat_ctx:
            context_str += (
                "\n\n[이 사람이 방금 묘묘에게 직접 한 말·고민 — 가장 중요한 단서]\n"
                f"{chat_ctx}\n"
                "→ 이 사람이 진짜 궁금해하고 걱정하는 걸 사주 근거로 콕 짚어 답해줘. "
                "리포트가 '내 얘기'처럼 느껴지게."
            )

        report_json: dict[str, Any] = {"$schema": "report.v2/career"}
        failed_sections: list[str] = []
        completed_count = 0

        for section_key in CAREER_V2_SECTION_KEYS:
            try:
                logger.info("saju_career_section_start", section=section_key)

                system_prompt, user_template = get_career_section_prompt(section_key)

                # careerStrategy: 앞 4섹션 summary 컨텍스트 주입(종합 섹션)
                effective_context = context_str
                if section_key == "careerStrategy":
                    prior_summaries = _collect_prior_summaries(
                        report_json, CAREER_V2_SECTION_KEYS, "careerStrategy",
                    )
                    if prior_summaries:
                        effective_context = (
                            context_str
                            + "\n\n[이전 섹션 분석 결과 요약]\n"
                            + "\n".join(prior_summaries)
                        )

                user_prompt = user_template.replace("{context_str}", effective_context)

                raw_response = await self._llm.generate(
                    prompt=user_prompt,
                    system=system_prompt,
                )

                llm_text = _extract_json(raw_response)
                report_json[section_key] = {"llmText": llm_text}
                completed_count += 1

                logger.info("saju_career_section_done", section=section_key)

            except Exception as e:
                logger.error(
                    "saju_career_section_failed",
                    section=section_key,
                    error=str(e),
                )
                failed_sections.append(section_key)

        total = len(CAREER_V2_SECTION_KEYS)

        if failed_sections:
            logger.warning(
                "saju_career_partial",
                completed=completed_count,
                total=total,
                failed=failed_sections,
            )

        return ToolResult.ok(
            data=report_json,
            report_type="career",
            schema_version="report.v2",
            sections_completed=completed_count,
            sections_total=total,
            failed_sections=failed_sections,
        )


def _collect_prior_summaries(
    report_json: dict,
    section_keys: list[str],
    stop_key: str,
) -> list[str]:
    """stop_key 이전 섹션들의 summary를 수집한다."""
    summaries: list[str] = []
    for key in section_keys:
        if key == stop_key:
            break
        llm_text = report_json.get(key, {}).get("llmText", {})
        summary = llm_text.get("summary")
        if summary:
            summaries.append(f"[{key}] {summary}")
    return summaries
