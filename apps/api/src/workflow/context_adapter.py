"""Workflow ContextAdapter: 서비스별 데이터 enrichment 플러그인.

범용 워크플로우 엔진은 dynamic 스텝 통찰을 만들 때 도메인 데이터(예: 사주 풀이,
궁합 결과)를 알 필요가 없다. 그런 도메인 결합은 ContextAdapter로 분리하고,
엔진은 `enrich()` 인터페이스만 호출한다.

어댑터는 프로파일(`config.context_adapter`)로 선택되어 세션에 바인딩되며,
부트스트랩에서 이름→인스턴스로 엔진에 주입된다.

설계 원칙:
- `enrich(collected)`의 반환값은 **LLM 프롬프트에 그대로 붙일 완성 블록**(라벨·지시문 포함)이다.
  엔진은 값들을 그대로 이어붙일 뿐, 도메인 라벨/문구를 알지 않는다.
- HTTP 등 외부 의존은 어댑터 내부에 가둔다. 실패는 조용히 삼키지 않되(로깅),
  해당 블록만 생략해 워크플로우 진행은 보장한다.
- 비싼 조회 결과는 `collected`의 `_`-prefix 키에 캐시해 dynamic 스텝마다 재호출하지 않는다
  (`_`-prefix는 엔진의 컨텍스트 표시 필터에서 자동 제외된다).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol, runtime_checkable

import httpx

from src.observability.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class WorkflowContextAdapter(Protocol):
    """워크플로우 dynamic 스텝용 도메인 컨텍스트 enrichment 인터페이스.

    필수: `enrich`. 선택(어댑터가 구현하면 엔진이 호출하는 hook):
    - `bind(session_id, collected)`: 세션 시작 시 session_id에서 도메인 식별자를
      추출해 collected에 주입(예: "saju-{uuid}-{product}" → collected["saju_id"]).
      엔진은 도메인별 세션ID 규약을 모른다 — 어댑터가 소유한다.
    - `cache_padding_text` (str 속성): 프롬프트 캐시 최소 크기 미달 시 채울
      도메인 배경 텍스트. 없으면 엔진이 도메인 중립 여백으로 패딩한다.
    """

    async def enrich(self, collected: dict) -> dict[str, str]:
        """collected 기반 추가 컨텍스트 블록을 반환한다.

        Returns:
            {블록키: 완성 블록 문자열} — LLM user 프롬프트에 그대로 이어붙일 텍스트.
            추가할 컨텍스트가 없으면 빈 dict.
        """
        ...


# 오행 영문 → 한글
_ELEMENT_KO = {
    "Wood": "목", "Fire": "화", "Earth": "토", "Metal": "금", "Water": "수",
    "wood": "목", "fire": "화", "earth": "토", "metal": "금", "water": "수",
}

# 세션ID에서 사주 UUID 추출. "saju-{uuid}" 및 "saju-{uuid}-{product}"(제품별 세션) 모두 대응.
# 이 포맷은 saju consumer(chat-orchestrator: `saju-${sajuId}-${product}`)가 정의한다.
_SAJU_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# 프롬프트 캐시 패딩용 사주 도메인 배경 사실. 캐시 안정 텍스트로만 쓰이며(말투·형식 무지정),
# 엔진이 cacheable_system 4096토큰 미달 시 filler로 주입한다. UUID/timestamp 포함 금지.
_SAJU_PADDING_TEXT = """

--- 배경 참고 자료 (사주 도메인 사실) ---
아래는 캐시 안정용 배경 지식일 뿐이다. 말투·호칭·이모지·문장 길이·응답 형식·캐릭터 정체성은
위 시스템 프롬프트(페르소나)를 그대로 따른다. 이 자료는 어떤 스타일·형식도 지시하지 않으며,
필요할 때 사실 확인용으로만 참고한다. 이 자료를 그대로 나열하거나 인용하지 않는다.

[오행(五行) 속성]
- 木(목): 성장·추진. 계절 봄, 방위 동쪽, 색 청색. 창의·개척의 기운.
- 火(화): 열정·표현. 계절 여름, 방위 남쪽, 색 적색. 명예·사교의 기운.
- 土(토): 안정·중심. 환절기, 방위 중앙, 색 황색. 신뢰·조율의 기운.
- 金(금): 결단·수확. 계절 가을, 방위 서쪽, 색 백색. 원칙·절제의 기운.
- 水(수): 지혜·흐름. 계절 겨울, 방위 북쪽, 색 흑색. 직관·적응의 기운.
- 상생(相生): 목생화·화생토·토생금·금생수·수생목.
- 상극(相剋): 목극토·토극수·수극화·화극금·금극목.

[천간(天干)]
갑(甲)·을(乙)=목, 병(丙)·정(丁)=화, 무(戊)·기(己)=토, 경(庚)·신(辛)=금, 임(壬)·계(癸)=수.
양간은 갑·병·무·경·임, 음간은 을·정·기·신·계.

[지지(地支)·십이지(十二支)]
자(子)·축(丑)·인(寅)·묘(卯)·진(辰)·사(巳)·오(午)·미(未)·신(申)·유(酉)·술(戌)·해(亥).
오행 배속: 인묘=목, 사오=화, 신유=금, 해자=수, 진술축미=토.

[십신(十神) 의미]
- 비견(比肩): 동등한 경쟁자, 독립심, 자존감.
- 겁재(劫財): 탈취형 경쟁, 충동성, 도전.
- 식신(食神): 표현·재능·여유, 복덕성.
- 상관(傷官): 반항·예술성·언변·감수성.
- 편재(偏財): 투기·사업·유동 자산.
- 정재(正財): 안정 수입·근면·고정 자산.
- 편관(偏官): 권력·도전·경쟁(칠살七殺).
- 정관(正官): 명예·법도·직책·사회적 책임.
- 편인(偏印): 직관·종교·신비(효신梟神).
- 정인(正印): 학문·모성·안정·보호(인수印綬).

[운(運)의 층위]
- 원국(原局): 타고난 사주 여덟 글자의 구조.
- 대운(大運): 약 10년 단위로 흐르는 큰 흐름.
- 세운(歲運): 한 해 단위의 흐름.
- 월운(月運)·일진(日辰): 달·날 단위의 미세한 흐름.
- 용신(用神): 사주의 균형을 돕는 핵심 기운. 기신(忌神)은 그 반대.
- 삼재(三災)·신살(神殺): 특정 시기·기운에 붙는 길흉의 상징적 표지.

[해석 일반 원칙(사실 차원)]
- 사주는 고정된 운명이 아니라 경향과 시기의 지도로 본다.
- 특정 날짜·인물·결과를 단정적으로 예언하지 않는다.
- 음양의 균형과 오행의 생극(生剋) 관계로 기운의 흐름을 읽는다.
--- 배경 참고 자료 끝 ---"""


class SajuContextAdapter:
    """사주 도메인 ContextAdapter.

    saju-backend의 lookup/궁합 결과를 조회해 dynamic 통찰용 한국어 컨텍스트 블록으로
    변환한다. backend URL은 생성자 주입(`settings.saju_backend_url`와 동일).
    """

    # 캐시 패딩용 도메인 배경 텍스트(엔진이 cacheable_system 미달 시 filler로 사용).
    cache_padding_text = _SAJU_PADDING_TEXT

    def __init__(self, backend_url: str) -> None:
        self._backend_url = backend_url.rstrip("/")

    def bind(self, session_id: str, collected: dict) -> None:
        """세션 시작 시 session_id에서 사주 UUID를 추출해 collected에 주입한다.

        saju consumer가 보내는 "saju-{uuid}-{product}" / "saju-{uuid}" 포맷을 파싱한다.
        이 규약은 saju 도메인 소유 — 범용 엔진은 알지 않는다.
        """
        m = _SAJU_UUID_RE.search(session_id)
        if m:
            collected["saju_id"] = m.group(0)
        elif session_id.startswith("saju-"):
            collected["saju_id"] = session_id[len("saju-"):]

    async def enrich(self, collected: dict) -> dict[str, str]:
        """사주 풀이 근거 + (있으면) 궁합 결과 블록을 반환한다.

        조회 결과는 collected의 `_saju_summary`/`_compat_summary`에 캐시해
        dynamic 스텝마다 재호출하지 않는다.
        """
        # 현재 날짜 블록 — saju_id 유무와 무관하게 항상 주입(연도 grounding).
        today = datetime.now()
        date_block = (
            f"\n\n[오늘 날짜] {today.year}년 {today.month}월 {today.day}일. "
            f"'올해'는 {today.year}년, '내년'은 {today.year + 1}년이다."
        )

        saju_id = collected.get("saju_id")
        if not saju_id:
            return {"date": date_block}

        # 사주 풀이 — 최초 1회 조회 후 캐시.
        if not collected.get("_saju_summary"):
            summary = await self._fetch_saju_summary(saju_id)
            if summary:
                collected["_saju_summary"] = summary
        # 궁합 — 분석이 실행됐다면(compat_job) 최초 1회 조회 후 캐시.
        if collected.get("compat_job") and not collected.get("_compat_summary"):
            compat = await self._fetch_compat_summary(saju_id)
            if compat:
                collected["_compat_summary"] = compat

        blocks: dict[str, str] = {"date": date_block}
        if collected.get("_saju_summary"):
            blocks["saju"] = (
                f"\n\n[이 사람의 실제 사주 풀이 근거 — 자연스러운 한국어로 녹여 쓰되 "
                f"숫자·영어·전문용어는 그대로 읊지 말 것]\n{collected['_saju_summary']}"
            )
        if collected.get("_compat_summary"):
            blocks["compat"] = (
                f"\n\n[상대방과의 실제 궁합 분석 결과 — 이 수치를 근거로 비교해 말하되 "
                f"점수 숫자를 직접 읊기보다 느낌으로 풀 것]\n{collected['_compat_summary']}"
            )
        return blocks

    async def _fetch_saju_summary(self, saju_id: str) -> str:
        """saju-backend의 lookup을 호출해 핵심 사주 결과를 한국어 요약으로 반환."""
        url = f"{self._backend_url}/saju/{saju_id}/lookup"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, params={"categories": "basic,wonGuk,energy,yongsin"})
                r.raise_for_status()
                d = r.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("saju_summary_fetch_failed", saju_id=saju_id, error=str(e))
            return ""

        wg = d.get("wonGuk", {}) or {}
        en = d.get("energy", {}) or {}
        ys = d.get("yongsin", {}) or {}
        ba = d.get("basic", {}) or {}
        ko = _ELEMENT_KO

        parts: list[str] = []
        self_el = wg.get("selfElement")
        status = wg.get("selfStatus")
        if self_el:
            st = "기운이 약한 편(신약)" if status == "weak" else ("기운이 강한 편(신강)" if status == "strong" else "")
            parts.append(f"타고난 중심 기운은 {ko.get(self_el, self_el)}, {st}".rstrip(", "))

        elems = {"목": en.get("wood"), "화": en.get("fire"), "토": en.get("earth"),
                 "금": en.get("metal"), "수": en.get("water")}
        elems = {k: v for k, v in elems.items() if isinstance(v, (int, float))}
        if elems:
            strong = max(elems, key=elems.get)
            weak = min(elems, key=elems.get)
            parts.append(f"기운 중 {strong}이(가) 가장 강하고 {weak}이(가) 가장 부족함")
        if ys.get("yongsin"):
            parts.append(
                f"채우면 좋은 기운은 {ko.get(ys['yongsin'], ys['yongsin'])}, "
                f"과하면 탈나는 기운은 {ko.get(ys.get('gisin',''), ys.get('gisin',''))}"
            )
        if ba.get("age"):
            parts.append(f"나이 {ba['age']}세")
        return " · ".join(p for p in parts if p)

    async def _fetch_compat_summary(self, saju_id: str) -> str:
        """saju-backend의 궁합 결과를 조회해 통찰용 한국어 요약(점수 등급)으로 반환."""
        url = f"{self._backend_url}/saju/compatibility/{saju_id}/result"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url)
                r.raise_for_status()
                data = (r.json() or {}).get("data", {}) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("compat_summary_fetch_failed", saju_id=saju_id, error=str(e))
            return ""

        score = data.get("score") or data.get("total_score")
        if not isinstance(score, (int, float)):
            return ""
        if score >= 80:
            grade = "아주 잘 맞는 편(상)"
        elif score >= 60:
            grade = "잘 맞는 편(중상)"
        elif score >= 40:
            grade = "노력하면 맞춰지는 편(중)"
        else:
            grade = "기질 차이가 큰 편(하)"
        return f"종합 궁합 {grade} (100점 만점 환산 약 {round(score)}점)"
