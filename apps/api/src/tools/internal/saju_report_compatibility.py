"""사주 Compatibility 리포트 생성 Tool.

ai-worker GeneratorNode._generate_compatibility_report_v2() 로직을
ai-platform Tool Protocol로 복사 후 async 변환.

6섹션(pillarsV4, energyV4, tenGodsShinsalV4, loveStrengthsV4,
fortuneV4, verdictV4) 순차 ���성.
"""

from __future__ import annotations

from typing import Any

from src.domain.agent_context import AgentContext
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.tools.base import ToolResult
from src.tools.internal.hanja_normalizer import normalize_llm_text
from src.tools.internal.saju_context_formatter import (
    format_single_person_context,
)
from src.tools.internal.saju_prompts import (
    COMPAT_V4_SECTION_KEYS,
    get_compat_section_prompt,
)
from src.tools.internal.saju_section_contract import SECTION_REQUIRED_KEYS, coerce_section

logger = get_logger(__name__)

_COMPAT_REQUIRED_SECTIONS = frozenset(COMPAT_V4_SECTION_KEYS)


class SajuReportCompatibilityTool:
    """report.v2/compatibility - 6섹션 순차 생성 Tool.

    Tool Protocol 준수: name, description, input_schema, execute().
    """

    name = "saju_report_compatibility"
    description = "사주 궁합 리포트를 report.v2 JSON 형태로 생성합니다."
    input_schema: dict = {
        "type": "object",
        "properties": {
            "saju_data": {
                "type": "object",
                "description": "궁합 사주 데이터 ({me: ..., partner: ...})",
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
        """6섹션 Compatibility 리포트를 순차 생성한다.

        params:
            saju_data: 궁합 사주 데이터 ({me: ..., partner: ...})

        Returns:
            ToolResult with report.v2/compatibility JSON
        """
        saju_data = params.get("saju_data")
        if not saju_data:
            return ToolResult.fail("saju_data가 필요합니다.")

        # 본인 + 상대 컨텍스트 병합
        context_str = _build_compatibility_context(saju_data)

        report_json: dict[str, Any] = {"$schema": "report.v2/compatibility"}
        failed_sections: list[str] = []
        # 생성은 됐지만 계약상 필수 키가 일부 빠진 섹션 — 내용은 살리되 조용히 넘기지 않는다.
        degraded_sections: list[str] = []
        completed_count = 0

        for section_key in COMPAT_V4_SECTION_KEYS:
            try:
                logger.info("saju_compat_section_start", section=section_key)

                system_prompt, user_template = get_compat_section_prompt(
                    section_key,
                )

                # verdictV4: 앞 섹션 summary 컨텍스트 주입
                effective_context = context_str
                if section_key == "verdictV4":
                    prior_summaries = _collect_prior_summaries(
                        report_json, COMPAT_V4_SECTION_KEYS, "verdictV4",
                    )
                    if prior_summaries:
                        effective_context = (
                            context_str
                            + "\n\n[이전 섹션 분석 결��� 요약]\n"
                            + "\n".join(prior_summaries)
                        )

                user_prompt = user_template.replace(
                    "{context_str}", effective_context,
                )

                # generate_json = 제약 디코딩(ollama format:"json") — 문법상 유효한 JSON을
                # 보장한다. generate()+_extract_json은 모델이 "순수 JSON만" 규칙을 어기면
                # 그대로 깨졌다(paper 실측: 7섹션 중 1~2개가 매번 실패).
                # 단, 제약 디코딩도 키 이름은 보장하지 않으므로 coerce_section으로 검증한다.
                parsed = await self._llm.generate_json(
                    prompt=user_prompt,
                    system=system_prompt,
                )

                parsed, missing = coerce_section(
                    parsed, report="compatibility", section=section_key,
                )
                if len(missing) == len(SECTION_REQUIRED_KEYS):
                    # 필수 키가 하나도 없다 = 섹션 형태가 아니다. 살릴 게 없으니 실패 처리.
                    raise ValueError(f"섹션 계약 위반 — 필수 키 전무: {sorted(parsed)[:5]}")

                llm_text = normalize_llm_text(parsed)
                report_json[section_key] = {"llmText": llm_text}
                completed_count += 1

                if missing:
                    # 일부만 빠졌으면 있는 내용은 살린다 — 통째로 버리면 멀쩡한 summary·
                    # advice까지 잃는다. 대신 조용히 넘기지 않고 남긴다.
                    degraded_sections.append(section_key)
                    logger.warning(
                        "saju_compat_section_degraded",
                        section=section_key,
                        missing_keys=missing,
                    )
                else:
                    logger.info("saju_compat_section_done", section=section_key)

            except Exception as e:
                logger.error(
                    "saju_compat_section_failed",
                    section=section_key,
                    error=str(e),
                )
                failed_sections.append(section_key)

        total = len(COMPAT_V4_SECTION_KEYS)

        if failed_sections:
            logger.warning(
                "saju_compat_partial",
                completed=completed_count,
                total=total,
                failed=failed_sections,
            )

        return ToolResult.ok(
            data=report_json,
            report_type="compatibility",
            schema_version="report.v2",
            sections_completed=completed_count,
            sections_total=total,
            failed_sections=failed_sections,
            degraded_sections=degraded_sections,
        )


def _build_compatibility_context(saju_data: dict) -> str:
    """궁합 데이터에서 두 사람의 컨텍스트를 생성한다."""
    if "me" in saju_data and "partner" in saju_data:
        me_ctx = format_single_person_context(saju_data["me"], "본인")
        partner_ctx = format_single_person_context(
            saju_data["partner"], "상대방",
        )
        return f"{me_ctx}\n\n{partner_ctx}"

    return format_single_person_context(saju_data, "본인")


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
