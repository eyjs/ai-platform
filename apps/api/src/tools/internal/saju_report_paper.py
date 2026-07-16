"""사주 Paper 리포트 생성 Tool.

ai-worker GeneratorNode._generate_paper_report_v2() 로직을
ai-platform Tool Protocol로 복사 후 async 변환.

7섹션(sajuWonguk, ohangYongsin, tenGodsShinsal, daewoonFlow,
loveRelation, careerWealth, verdictV2) 순차 생성.
"""

from __future__ import annotations

from typing import Any

from src.domain.agent_context import AgentContext
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.tools.base import ToolResult
from src.tools.internal.hanja_normalizer import normalize_llm_text
from src.tools.internal.saju_context_formatter import format_single_person_context
from src.tools.internal.saju_prompts import PAPER_V2_SECTION_KEYS, get_paper_section_prompt
from src.tools.internal.saju_section_contract import SECTION_REQUIRED_KEYS, coerce_section

logger = get_logger(__name__)

_PAPER_REQUIRED_SECTIONS = frozenset(PAPER_V2_SECTION_KEYS)


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

        report_json: dict[str, Any] = {"$schema": "report.v2/paper"}
        failed_sections: list[str] = []
        # 생성은 됐지만 계약상 필수 키가 일부 빠진 섹션 — 내용은 살리되 조용히 넘기지 않는다.
        degraded_sections: list[str] = []
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

                # generate_json = 제약 디코딩(ollama format:"json") — 문법상 유효한 JSON을
                # 보장한다. generate()+_extract_json은 모델이 규칙("순수 JSON만")을 어기면
                # 그대로 깨졌다(실측: 7섹션 중 1~2개가 매번 실패 — 따옴표 미이스케이프,
                # JSON 뒤 잡텍스트, JSON 자체 누락).
                # 단, 제약 디코딩은 문법만 보장하고 키 이름은 보장하지 않는다 → 아래 검증.
                parsed = await self._llm.generate_json(
                    prompt=user_prompt,
                    system=system_prompt,
                )

                parsed, missing = coerce_section(parsed, report="paper", section=section_key)
                if len(missing) == len(SECTION_REQUIRED_KEYS):
                    # 필수 키가 하나도 없다 = 섹션 형태가 아니다. 살릴 게 없으니 실패 처리.
                    raise ValueError(f"섹션 계약 위반 — 필수 키 전무: {sorted(parsed)[:5]}")

                llm_text = normalize_llm_text(parsed)
                report_json[section_key] = {"llmText": llm_text}
                completed_count += 1

                if missing:
                    # 일부만 빠졌으면 있는 내용은 살린다 — 통째로 버리면 멀쩡한 summary·
                    # advice까지 잃는다. 대신 조용히 넘기지 않고 남긴다(원래 문제가 그거였다).
                    degraded_sections.append(section_key)
                    logger.warning(
                        "saju_paper_section_degraded",
                        section=section_key,
                        missing_keys=missing,
                    )
                else:
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
            degraded_sections=degraded_sections,
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
