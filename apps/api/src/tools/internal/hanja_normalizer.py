"""한자(漢字) → 한글 정규화 — 사주 리포트/응답에서 한자 노출 차단.

배경: 사주 LLM 출력에 십성 글로스(正財·偏財), 천간지지(己巳·丙寅), 오행(木火土金水)
등 한자가 섞여 나온다. 프롬프트 규칙만으론(rule10) 모델이 무시 → 결정론적 후처리로 차단.

정책:
- 사주 도메인 한자(천간·지지·오행·십성·신살·운 등)는 한글 음으로 변환(己巳→기사, 正財→정재).
- 매핑에 없는 CJK 한자는 제거(strip) — "한자 노출 0" 보장.
- "정재(正財)"처럼 한글+한자 글로스 중복은 변환 후 "정재(정재)"→"정재"로 정리.
- 변환/제거로 생긴 빈 괄호 ()（）「」【】 정리.
"""

from __future__ import annotations

import re
from typing import Any

# 사주 도메인 한자 → 한글 음. 천간/지지/오행/십성 + 자주 쓰는 신살·운 글자.
_HANJA_MAP: dict[str, str] = {
    # 천간(天干) 10
    "甲": "갑", "乙": "을", "丙": "병", "丁": "정", "戊": "무",
    "己": "기", "庚": "경", "辛": "신", "壬": "임", "癸": "계",
    # 지지(地支) 12
    "子": "자", "丑": "축", "寅": "인", "卯": "묘", "辰": "진", "巳": "사",
    "午": "오", "未": "미", "申": "신", "酉": "유", "戌": "술", "亥": "해",
    # 오행(五行) 5
    "木": "목", "火": "화", "土": "토", "金": "금", "水": "수",
    # 십성(十星) 구성 글자 — 比肩/劫財/食神/傷官/偏財/正財/偏官/正官/偏印/正印
    "比": "비", "肩": "견", "劫": "겁", "財": "재", "食": "식", "神": "신",
    "傷": "상", "官": "관", "偏": "편", "正": "정", "印": "인",
    # 음양·방위·기타 도메인 자주
    "陰": "음", "陽": "양", "東": "동", "西": "서", "南": "남", "北": "북", "中": "중",
    # 신살·운(자주 노출)
    "桃": "도", "花": "화", "驛": "역", "馬": "마", "華": "화", "蓋": "개",
    "文": "문", "昌": "창", "貴": "귀", "人": "인", "殺": "살", "刃": "인",
    "沖": "충", "合": "합", "刑": "형", "害": "해", "破": "파", "空": "공",
    "祿": "록", "羊": "양", "災": "재", "大": "대", "運": "운", "歲": "세",
    "命": "명", "三": "삼", "六": "육", "用": "용", "喜": "희", "忌": "기",
}

# CJK 한자(기본+확장A) 탐지 — 매핑에 없으면 제거.
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
# 변환 후 중복 글로스 "정재(정재)" → "정재"
_DUP_GLOSS_RE = re.compile(r"([가-힣]+)\s*[\(（]\s*\1\s*[\)）]")
# 빈 괄호류 정리
_EMPTY_PAREN_RE = re.compile(r"[\(（\[\「【〔]\s*[\)）\]\」】〕]")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")


def to_hangul(text: str) -> str:
    """문자열의 한자를 한글로 변환하고, 매핑 없는 CJK는 제거한다."""
    if not text:
        return text
    chars: list[str] = []
    for ch in text:
        mapped = _HANJA_MAP.get(ch)
        if mapped is not None:
            chars.append(mapped)
        elif _CJK_RE.match(ch):
            continue  # 매핑 없는 한자 제거(노출 0 보장)
        else:
            chars.append(ch)
    s = "".join(chars)
    # "정재(정재)" 같은 중복 글로스 제거(반복 적용으로 다중 중첩도 정리)
    prev = None
    while prev != s:
        prev = s
        s = _DUP_GLOSS_RE.sub(r"\1", s)
    s = _EMPTY_PAREN_RE.sub("", s)
    s = _MULTISPACE_RE.sub(" ", s)
    return s.strip()


def normalize_llm_text(value: Any) -> Any:
    """llmText(dict/list/str 중첩)의 모든 문자열에 to_hangul을 재귀 적용한다."""
    if isinstance(value, dict):
        return {k: normalize_llm_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_llm_text(v) for v in value]
    if isinstance(value, str):
        return to_hangul(value)
    return value
