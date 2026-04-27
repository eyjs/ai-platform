"""사주 Paper 리포트 생성 Tool.

ai-worker GeneratorNode._generate_paper_report_v2() 로직을
ai-platform Tool Protocol로 복사 후 async 변환.

7섹션(sajuWonguk, ohangYongsin, tenGodsShinsal, daewoonFlow,
loveRelation, careerWealth, verdictV2) 순차 생성.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.infrastructure.providers.base import LLMProvider
from src.tools.base import ToolResult
from src.tools.internal.saju_context_formatter import format_single_person_context
from src.tools.internal.saju_prompts import PAPER_V2_SECTION_KEYS, get_paper_section_prompt

logger = logging.getLogger(__name__)

_PAPER_REQUIRED_SECTIONS = frozenset(PAPER_V2_SECTION_KEYS)


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


class SajuReportPaperTool:
    """report.v2/paper - 7섹션 순차 생성 Tool.

    Tool Protocol 준수: name, description, input_schema, execute().
    """

    name = "saju_report_paper"
    description = "사주 Paper 리포트를 report.v2 JSON 형태로 생성합니다."
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
        """7섹션 Paper 리포트를 순차 생성한다.

        params:
            saju_data: 사주 원천 데이터 dict

        Returns:
            ToolResult with report.v2/paper JSON
        """
        saju_data = params.get("saju_data")
        if not saju_data:
            return ToolResult.fail("saju_data가 필요합니다.")

        context_str = format_single_person_context(saju_data, "사용자")

        report_json: dict[str, Any] = {"$schema": "report.v2/paper"}
        failed_sections: list[str] = []
        completed_count = 0

        for section_key in PAPER_V2_SECTION_KEYS:
            try:
                logger.info("saju_paper_section_start", section=section_key)

                system_prompt, user_template = get_paper_section_prompt(section_key)

                # verdictV2: 앞 섹션 summary 컨텍스트 주입
                effective_context = context_str
                if section_key == "verdictV2":
                    prior_summaries = _collect_prior_summaries(
                        report_json, PAPER_V2_SECTION_KEYS, "verdictV2",
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

                logger.info("saju_paper_section_done", section=section_key)

            except Exception as e:
                logger.error(
                    "saju_paper_section_failed",
                    section=section_key,
                    error=str(e),
                )
                failed_sections.append(section_key)

        total = len(PAPER_V2_SECTION_KEYS)

        if failed_sections:
            logger.warning(
                "saju_paper_partial",
                completed=completed_count,
                total=total,
                failed=failed_sections,
            )

        return ToolResult.ok(
            data=report_json,
            report_type="paper",
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
