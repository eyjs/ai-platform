"""키워드 토큰 경계 매칭 — 부분문자열 오탐 제거.

배경(아키텍처 진단 2026-07-15 V4/V6): 라우팅 키워드를 전부 `pattern in query`
부분문자열로 봤다. 진단의 표현대로 병증은 "제멋대로 바뀐다"가 아니라 "**틀린 곳으로
확정적으로 샌다**"였다.
- V4: flowsns-ops의 1글자 패턴 "건"이 조건·안건·건강·물건에 전부 매칭 →
  "이 조건이 궁금해요"가 TASK 인텐트로 오태깅.
- V6: 비교 마커 "차이"가 **차이나타운**에 매칭 →
  "차이나타운 화재보험 있어요?"가 다문서 비교 전략으로 승격.

**왜 완전일치가 아닌가**: 한국어는 교착어이자 합성어가 흔해, 완전일치는 정상 케이스를
대량으로 놓친다. 두 축을 따로 허용한다.

① 꼬리(조사·어미) — 토큰 뒤에 붙는 문법 요소:
  - "차이가"    = "차이" + "가"(조사)        → 매칭 ✅
  - "차이나타운" = "차이" + "나타운"(조사 아님) → 미매칭 ✅
  꼬리를 **화이트리스트**로 묶는 게 요점이다 — 길이만 재면 "차이나"(2자)가 통과한다.

② 합성어 **머리**(키워드가 뒤) — 한국어 합성어는 수식어+키워드 순이다:
  - "자동차보험" endswith "보험"  → 매칭 ✅  (없으면 보험 라우팅에 큰 구멍)
  - "차이나타운" endswith "차이"? → 미매칭 ✅
  - "작업량"    endswith "작업"? → 미매칭 ✅ (작업량은 다른 낱말)

**의도적 비대칭 — 키워드가 앞에 오는 합성어는 매칭하지 않는다**:
  - "보험금"(보험+금), "보험사"(보험+사)는 패턴 "보험"으로 안 잡힌다.
  이유: 그걸 허용하려면 `startswith(pattern) + 임의 꼬리`를 열어야 하는데, 그러면
  "차이나타운"(차이+나타운)과 문자열로 구별할 방법이 없어 V6가 그대로 되살아난다.
  형태소 분석기 없이는 둘이 같은 모양이다. 그래서 오탐을 막는 쪽을 택했고,
  대신 자주 쓰는 앞머리 합성어는 프로필이 패턴으로 명시한다
  (insurance-qa가 이미 "보험금"·"보험료"를 따로 둔 것이 그 예).
"""

from __future__ import annotations

import re

# 패턴 최소 길이. 1글자 패턴은 어떤 매칭 규칙을 써도 오탐을 못 막는다
# ("건"→조건/물건은 토큰 경계로도 못 거른다. '조건'이 한 토큰이라 꼬리 규칙이 안 통함).
# 진단 P1: "2글자 미만 패턴 로드 시 거부".
MIN_PATTERN_LENGTH = 2

# 토큰 분해 — ko.yaml의 tokenize와 같은 눈금(한글/영문 2자 이상 연속).
# 숫자·기호는 키워드 매칭 대상이 아니라 제외한다.
_TOKEN_RE = re.compile(r"[가-힣a-zA-Z]{2,}")

# 패턴 뒤에 붙어도 같은 낱말로 보는 꼬리(조사·어미). 화이트리스트인 게 핵심 —
# "길이 ≤ N" 같은 규칙으로 대체하면 "차이나"(2자)가 통과해 V6가 되살아난다.
_TAILS = frozenset({
    # 조사
    "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "과", "와", "로", "랑",
    "에서", "에게", "으로", "이랑", "부터", "까지", "보다", "처럼", "마다", "한테",
    "이나", "이란", "이라", "라는", "이든", "밖에", "조차",
    # 종결·연결 어미 (패턴이 용언 어간인 경우: "비교해"+"줘")
    "요", "야", "여", "죠", "지", "네", "다", "고", "서", "며", "면", "니", "나",
    "줘", "봐", "서요", "해줘", "했어", "하고", "하는", "한다", "인가", "인지", "일까",
})


def tokenize(text: str) -> list[str]:
    """키워드 매칭용 토큰. ko.yaml tokenize와 같은 규칙."""
    return _TOKEN_RE.findall(text or "")


def _strip_tail(token: str) -> str:
    """토큰에서 조사·어미 꼬리를 한 번 떼어낸다. 없으면 원본."""
    for tail in _TAILS:
        if len(token) > len(tail) and token.endswith(tail):
            return token[: -len(tail)]
    return token


def _token_hits(token: str, pattern: str) -> bool:
    if token == pattern:
        return True
    # ① 꼬리 허용: "차이" + "가" → 매칭 / "차이" + "나타운" → 미매칭
    if token.startswith(pattern) and token[len(pattern):] in _TAILS:
        return True
    # ② 합성어 머리 허용: "자동차보험" → "보험". 꼬리를 뗀 뒤에도 본다("자동차보험을").
    # endswith만 본다 — 중간 포함까지 열면 부분문자열 매칭으로 되돌아간다.
    return _strip_tail(token).endswith(pattern)


def is_valid_pattern(pattern: str) -> bool:
    """로드 가능한 패턴인지. 공백 포함 구(句)는 토큰이 아니라 원문에서 찾으므로 통과."""
    return len(pattern.strip()) >= MIN_PATTERN_LENGTH


def matches(query: str, pattern: str, *, tokens: list[str] | None = None) -> bool:
    """query가 pattern을 '낱말로' 포함하는지.

    공백이 든 패턴("할 일", "다른 점")은 토큰 단위로 쪼갤 수 없어 원문 부분문자열로
    본다 — 구(句)는 그 자체로 충분히 길어 오탐 위험이 낮다.
    1글자 패턴은 매칭하지 않는다(is_valid_pattern 참조).
    """
    pattern = (pattern or "").strip()
    if not is_valid_pattern(pattern):
        return False
    if " " in pattern:
        return pattern in (query or "")
    toks = tokenize(query) if tokens is None else tokens
    return any(_token_hits(t, pattern) for t in toks)


def matches_any(query: str, patterns, *, tokens: list[str] | None = None) -> bool:
    toks = tokenize(query) if tokens is None else tokens
    return any(matches(query, p, tokens=toks) for p in patterns)
