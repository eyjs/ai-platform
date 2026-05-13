"""사주 데이터 조회 도구 — AI 에이전트가 필요한 사주 데이터를 카테고리별로 조회한다."""

from __future__ import annotations

import logging

import httpx

from src.tools.base import ToolResult

logger = logging.getLogger(__name__)

VALID_CATEGORIES = [
    "basic",
    "wonGuk",
    "energy",
    "yongsin",
    "tenGods",
    "shinsal",
    "saewoon",
    "daewoon",
    "samjae",
    "daily",
]


class SajuLookupTool:
    """사주 데이터를 카테고리별로 조회하는 도구.

    Tool Protocol 준수: name, description, input_schema, execute().
    """

    name = "saju_lookup"
    description = (
        "내담자의 사주 데이터를 카테고리별로 조회합니다. "
        "필요한 카테고리만 선택하여 효율적으로 데이터를 가져옵니다.\n"
        "카테고리 종류:\n"
        "- basic: 이름, 성별, 나이 등 기본 정보\n"
        "- wonGuk: 사주 원국 (년월일시 사주, 일간, 신강약)\n"
        "- energy: 오행 에너지 분포 및 과다/부족 분석\n"
        "- yongsin: 용신, 희신, 기신 정보\n"
        "- tenGods: 십성 배치\n"
        "- shinsal: 신살 목록\n"
        "- saewoon: 올해 세운 및 합충 상호작용\n"
        "- daewoon: 현재 대운 (10년 주기)\n"
        "- samjae: 삼재 해당 여부\n"
        "- daily: 오늘의 일진 및 용신/기신 관계"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "categories": {
                "type": "array",
                "items": {"type": "string", "enum": VALID_CATEGORIES},
                "description": "조회할 카테고리 목록. 필요한 것만 선택하세요.",
                "minItems": 1,
            },
        },
        "required": ["categories"],
    }

    def __init__(self, backend_url: str) -> None:
        self._backend_url = backend_url.rstrip("/")

    async def execute(self, params: dict, context) -> ToolResult:
        """카테고리별 사주 데이터를 조회한다.

        params:
            categories: 조회할 카테고리 목록

        context:
            session_id: "saju-{uuid}" 형식의 세션 ID (saju_id 추출에 사용)

        Returns:
            ToolResult with 카테고리별 사주 데이터 dict
        """
        categories: list[str] = params.get("categories", [])
        if not categories:
            return ToolResult(success=False, error="카테고리를 하나 이상 지정하세요.")

        # 유효하지 않은 카테고리 검증
        invalid = [c for c in categories if c not in VALID_CATEGORIES]
        if invalid:
            return ToolResult(
                success=False,
                error=f"유효하지 않은 카테고리: {invalid}. 허용된 값: {VALID_CATEGORIES}",
            )

        # session_id에서 saju_id 추출 (format: "saju-{uuid}")
        session_id: str = getattr(context, "session_id", "") or ""
        saju_id = session_id.removeprefix("saju-") if session_id.startswith("saju-") else ""

        if not saju_id:
            return ToolResult(
                success=False,
                error="세션에서 사주 ID를 추출할 수 없습니다.",
            )

        categories_str = ",".join(categories)
        url = f"{self._backend_url}/saju/{saju_id}/lookup?categories={categories_str}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning(
                "saju_lookup HTTP error: %s %s",
                e.response.status_code,
                e.response.text[:200],
            )
            return ToolResult(
                success=False,
                error=f"사주 데이터 조회 실패: HTTP {e.response.status_code}",
            )
        except Exception as e:
            logger.exception("saju_lookup failed")
            return ToolResult(success=False, error=f"사주 데이터 조회 중 오류: {str(e)}")

        logger.info("saju_lookup_success saju_id=%s categories=%s", saju_id, ",".join(categories))
        return ToolResult(success=True, data=data)
