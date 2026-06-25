"""GEPA PoC — 사주 섹션 결정론적 metric.

[Addendum F] 모든 규칙은 src/tools/internal/saju_prompts.py 의 실제 `_COMMON_RULES`
(127-142행)에서 전사했다. 코드에 없는 규칙을 새로 발명하지 않는다.

전사한 규칙(요약):
  rule1  순수 JSON, 마크다운 코드블록(```) 금지
  rule2  필드 {summary, advice, conclusion, characteristics}
  rule3  말투 묘묘·반말·상대를 '너'라고 호칭          ← "너 호칭"은 실제 규칙(확인)
  rule4  개인화: 일반론/교과서 금지, '너는~'으로 실제 데이터 근거
  rule6  summary 필수 150-320자
  rule7  advice  필수 80-160자
  rule8  conclusion 필수 30-70자
  rule9  characteristics 선택 150-280자
  rule10 순수 한국어, 영문 변수명(DIRECT_WEALTH 등) 노출 금지

[텐션 — 사람 판단 필요] `_COMMON_RULES`는 conclusion 을 **필수**로 규정하나,
get_paper_section_prompt 의 user 텍스트(saju_prompts.py:166)는 "summary·advice 필수"만
명시한다. 또한 verdictV2 섹션 지침은 summary "4-5문장"을 요구해 rule6(150-320자)와
충돌할 수 있다. 본 metric은 `_COMMON_RULES`를 진실원천으로 삼아 conclusion 을 필수
처리하되, 이 불일치를 리포트에 노출하고 사람의 최종 판단을 받는다.

metric은 형식·개인화 "규칙 준수"를 측정할 뿐 "글을 잘 썼는가(가독성/품질)"를 측정하지
않는다(Goodhart 위험). 품질 신호는 사람 블라인드(Addendum B)로만 다룬다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 런타임 JSON 추출 로직을 그대로 재사용 — 운영 파싱과 동일 거동 보장.
from src.tools.internal.saju_report_paper import _extract_json

# ── _COMMON_RULES 전사: 길이 밴드 (saju_prompts.py:137-140) ──────────────
LENGTH_BANDS: dict[str, tuple[int, int]] = {
    "summary": (150, 320),   # rule6 필수
    "advice": (80, 160),     # rule7 필수
    "conclusion": (30, 70),  # rule8 필수
    "characteristics": (150, 280),  # rule9 선택
}
REQUIRED_FIELDS = ("summary", "advice", "conclusion")  # rule6·7·8 (진실원천=_COMMON_RULES)
OPTIONAL_FIELDS = ("characteristics",)

# rule10: 영문 변수명(SCREAMING_SNAKE) 노출 금지 — DIRECT_WEALTH, FOOD_GOD 등
_ENGLISH_VARNAME_RE = re.compile(r"\b[A-Z][A-Z_]{3,}\b")
_CODE_FENCE_RE = re.compile(r"```")

# 가중치 (합 = 1.0)
WEIGHTS = {
    "json_valid": 0.25,
    "required_fields": 0.20,
    "length": 0.25,
    "personalization": 0.20,
    "no_leakage": 0.10,
}


@dataclass
class ScoreResult:
    """단일 응답 채점 결과 — 점수 + GEPA reflection용 텍스트 피드백 + 성분 분해."""

    score: float
    feedback: str
    breakdown: dict[str, float] = field(default_factory=dict)
    parsed: dict | None = None


def _band_score(text: str, lo: int, hi: int) -> float:
    """길이 밴드까지의 연속 거리 점수(gradient 확보). 밴드 내=1.0, 밖=선형 감쇠."""
    n = len(text.strip())
    if lo <= n <= hi:
        return 1.0
    if n < lo:
        return max(0.0, 1.0 - (lo - n) / max(1, lo))
    return max(0.0, 1.0 - (n - hi) / max(1, hi))


def _personalization_score(
    answer: str, pers: dict,
) -> tuple[float, list[str], list[str]]:
    """rule3·4: '너' 호칭 + 이 사람 실제 데이터 토큰 언급 비율.

    [사용자 결정: 변형 허용] 핵심(core) 토큰만 비율의 분모로 쓰고, 일간은 변형
    표기(갑/甲/갑목 등)를 허용한다. 일주 compound·신강 status는 bonus(가점)로만.

    pers = {"core": [...], "bonus": [...], "variants": {token: [표기변형...]}}

    Returns: (점수[0,1], core 언급, core 누락)
    """
    core = [t for t in pers.get("core", []) if t]
    bonus = [t for t in pers.get("bonus", []) if t]
    variants = pers.get("variants", {})

    def _hit(tok: str) -> bool:
        if tok in answer:
            return True
        return any(v in answer for v in variants.get(tok, []))

    core_hit = [t for t in core if _hit(t)]
    core_missed = [t for t in core if not _hit(t)]
    ratio = len(core_hit) / max(1, len(core))

    # bonus는 분모 밖 — 맞히면 소량 가점(최대 +0.10), 상한 1.0
    if bonus:
        bonus_hit = sum(1 for t in bonus if _hit(t))
        ratio = min(1.0, ratio + 0.10 * bonus_hit / len(bonus))

    # rule3 '너' 호칭 없으면 개인화 상한 0.5 (일반론 방어)
    if "너" not in answer:
        ratio = min(ratio, 0.5)
    return ratio, core_hit, core_missed


def score_saju_section(
    section_key: str,
    raw_response: str,
    pers: dict,
) -> ScoreResult:
    """사주 섹션 LLM 응답을 결정론적으로 채점한다.

    Args:
        section_key: 섹션 키(피드백 라벨용)
        raw_response: LLM 원응답(JSON 문자열 기대)
        pers: 개인화 채점 스펙 {"core":[...], "bonus":[...], "variants":{...}}.
            core=우세오행·용신·일간·신살(분모), bonus=일주compound·신강(가점),
            variants=일간 변형 표기 허용 맵.

    Returns:
        ScoreResult(score, feedback, breakdown, parsed)
    """
    parts: dict[str, float] = {}
    notes: list[str] = []

    # ── rule1: 코드펜스 / rule10: 영문 변수명 누수 ──
    fence = bool(_CODE_FENCE_RE.search(raw_response))
    varnames = sorted(set(_ENGLISH_VARNAME_RE.findall(raw_response)))
    leakage_penalty = (0.5 if fence else 0.0) + (0.5 if varnames else 0.0)
    parts["no_leakage"] = max(0.0, 1.0 - leakage_penalty)
    if fence:
        notes.append("코드펜스(```) 노출 — rule1 위반")
    if varnames:
        notes.append(f"영문 변수명 노출 {varnames[:4]} — rule10 위반")

    # ── rule1·2: JSON 파싱 ──
    try:
        parsed = _extract_json(raw_response)
        if not isinstance(parsed, dict):
            raise ValueError("최상위가 dict 아님")
        parts["json_valid"] = 1.0
    except Exception as e:
        parts["json_valid"] = 0.0
        notes.append(f"JSON 파싱 실패({type(e).__name__}) — rule1·2 위반")
        # 파싱 실패 시 필드/길이 측정 불가 → 0 처리, 개인화는 raw에서 측정
        parts["required_fields"] = 0.0
        parts["length"] = 0.0
        pscore, hit, missed = _personalization_score(raw_response, pers)
        parts["personalization"] = pscore
        notes.append(f"개인화 {pscore:.2f}(core 언급 {len(hit)}/{len(hit) + len(missed)})")
        return _finalize(section_key, parts, notes, None)

    # ── rule6·7·8: 필수 필드 존재 ──
    present = [f for f in REQUIRED_FIELDS if str(parsed.get(f, "")).strip()]
    parts["required_fields"] = len(present) / len(REQUIRED_FIELDS)
    missing = [f for f in REQUIRED_FIELDS if f not in present]
    if missing:
        notes.append(f"필수필드 누락 {missing} — rule6·7·8")

    # ── rule6·7·8·9: 길이 밴드(연속) ──
    band_scores: list[float] = []
    for f, (lo, hi) in LENGTH_BANDS.items():
        val = str(parsed.get(f, "")).strip()
        if f in OPTIONAL_FIELDS and not val:
            continue  # 선택 필드 미존재는 감점 안 함
        if not val:
            band_scores.append(0.0)
            continue
        bs = _band_score(val, lo, hi)
        band_scores.append(bs)
        if bs < 1.0:
            notes.append(f"{f} {len(val)}자 — 밴드[{lo},{hi}] 이탈({bs:.2f})")
    parts["length"] = sum(band_scores) / max(1, len(band_scores))

    # ── rule3·4: 개인화(파싱된 필드 텍스트 기준) ──
    answer_text = " ".join(
        str(parsed.get(f, "")) for f in (*REQUIRED_FIELDS, *OPTIONAL_FIELDS)
    )
    pscore, hit, missed = _personalization_score(answer_text, pers)
    parts["personalization"] = pscore
    if pscore < 1.0:
        notes.append(
            f"개인화 {pscore:.2f}(core 언급 {hit[:5]} / 누락 {missed[:5]}) — rule3·4"
        )

    return _finalize(section_key, parts, notes, parsed)


def _finalize(
    section_key: str,
    parts: dict[str, float],
    notes: list[str],
    parsed: dict | None,
) -> ScoreResult:
    total = sum(WEIGHTS[k] * parts.get(k, 0.0) for k in WEIGHTS)
    if not notes:
        feedback = f"[{section_key}] 모든 규칙 충족(점수 {total:.2f})."
    else:
        feedback = f"[{section_key}] 점수 {total:.2f} — " + "; ".join(notes)
    return ScoreResult(score=round(total, 4), feedback=feedback, breakdown=parts, parsed=parsed)
