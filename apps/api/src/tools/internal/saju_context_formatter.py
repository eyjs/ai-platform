"""사주 데이터 포맷팅 헬퍼.

ai-worker GeneratorNode._format_single_person_context 복사 후 정리.
saju_report_paper / saju_report_compatibility 에서 공유한다.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def format_single_person_context(data: dict, label: str = "사용자") -> str:
    """단일 사주 데이터를 텍스트로 변환한다.

    Args:
        data: 사주 원천 데이터 (basic, premium 포함)
        label: 컨텍스트 내 라벨 (예: "사용자", "본인", "상대방")

    Returns:
        텍스트 형태의 사주 컨텍스트 문자열
    """
    try:
        basic = data.get("basic", {})
        pillars = basic.get("fourPillars", {})
        interp = data.get("premium", {}).get("interpretation", {})
        energy = interp.get("energyScore", {})
        yongsin = interp.get("yongsin", {})
        shinsal_list = interp.get("shinsal", [])

        if not isinstance(shinsal_list, list):
            shinsal_list = []

        year = pillars.get("year", {})
        month = pillars.get("month", {})
        day = pillars.get("day", {})
        hour = pillars.get("hour", {})

        context = (
            f"[{label} 사주 마스터 데이터]\n"
            f"- 이름: {basic.get('name', '미상')}\n"
            f"- 성별: {basic.get('gender')}\n"
            f"- 생년월일: {basic.get('birthDate')}\n"
            f"- 사주 원국:\n"
            f"    * 년주: {year.get('heavenlyStem')}{year.get('earthlyBranch')}\n"
            f"    * 월주: {month.get('heavenlyStem')}{month.get('earthlyBranch')}\n"
            f"    * 일주: {day.get('heavenlyStem')}{day.get('earthlyBranch')} (본인)\n"
            f"    * 시주: {hour.get('heavenlyStem')}{hour.get('earthlyBranch')}\n"
            f"- 오행 분포: "
            f"목({energy.get('wood')}), "
            f"화({energy.get('fire')}), "
            f"토({energy.get('earth')}), "
            f"금({energy.get('metal')}), "
            f"수({energy.get('water')})\n"
            f"- 신강약: {energy.get('selfStatus')} "
            f"(강도: {energy.get('selfStrength')})\n"
            f"- 용신(필요한 기운): {yongsin.get('yongsin')} "
            f"(전략: {yongsin.get('strategy')})\n"
            f"- 신살: {', '.join(shinsal_list)}"
        )
        return context.strip()

    except Exception as e:
        logger.error("saju_context_format_error", label=label, error=str(e))
        return f"[{label}] 데이터 파싱 중 오류가 발생했습니다."


def format_context(data: dict) -> str:
    """사주 원천 데이터를 텍스트로 변환한다.

    궁합(me/partner)과 단일 인물을 자동 구분한다.
    """
    if not data:
        return "데이터가 제공되지 않았습니다."

    try:
        if "me" in data and "partner" in data:
            me_ctx = format_single_person_context(data["me"], "본인")
            partner_ctx = format_single_person_context(data["partner"], "상대방")
            return f"{me_ctx}\n\n{partner_ctx}"

        return format_single_person_context(data, "사용자")

    except Exception as e:
        logger.error("saju_context_format_error", error=str(e))
        return "데이터 파싱 중 오류가 발생했습니다."
