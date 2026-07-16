"""토큰 경계 매칭 (V4/V6) — 부분문자열 오탐 제거.

진단(2026-07-15): 라우팅 키워드가 전부 `pattern in query`였다.
- V4: 1글자 "건" → 조건/안건/건강/물건 전부 매칭
- V6: "차이" → **차이나타운** 매칭 → 불필요한 다문서 비교 전략 승격

오탐만 막고 끝내면 실패다 — 한국어 합성어("자동차보험")와 조사("차이가")를 계속
잡아야 라우팅에 구멍이 안 난다. 양방향을 함께 고정한다.
"""

import pytest

from src.router.token_match import (
    MIN_PATTERN_LENGTH,
    is_valid_pattern,
    matches,
    matches_any,
    tokenize,
)


# --- 진단서 오탐 (반드시 미매칭) ---


@pytest.mark.parametrize("query,pattern,why", [
    ("차이나타운 화재보험 있어요?", "차이", "V6 — 차이나타운은 '차이'가 아니다"),
    ("이 조건이 궁금해요", "건", "V4 — 1글자 패턴"),
    ("물건 등록해줘", "건", "V4 — 1글자 패턴"),
    ("건강검진 받았어", "건", "V4 — 1글자 패턴"),
    ("안건 정리해줘", "건", "V4 — 1글자 패턴"),
    ("작업량이 많아", "작업", "작업량은 다른 낱말"),
    ("보험사기 조사중", "보험사", "보험사기는 보험사가 아니다"),
])
def test_audit_false_positives_are_gone(query, pattern, why):
    assert matches(query, pattern) is False, why


# --- 정상 매칭 (반드시 매칭) ---


@pytest.mark.parametrize("query,pattern,why", [
    ("두 상품 차이가 뭐야", "차이", "조사 '가'"),
    ("이 둘의 차이를 비교해줘", "차이", "조사 '를'"),
    ("차이 알려줘", "차이", "완전일치"),
    ("A랑 B 비교해줘", "비교해", "어미 '줘'"),
    ("작업을 등록해줘", "작업", "조사 '을'"),
    ("작업 등록해줘", "작업", "완전일치"),
])
def test_normal_matches_survive(query, pattern, why):
    assert matches(query, pattern) is True, why


@pytest.mark.parametrize("query,pattern", [
    ("자동차보험 대인배상 절차 알려줘", "보험"),
    ("화재보험 있어요?", "보험"),
    ("실손보험 보장 범위 알려줘", "보험"),
    ("자동차보험을 알아보고 싶어", "보험"),
])
def test_compound_head_matches(query, pattern):
    """한국어 합성어는 키워드가 뒤에 온다(자동차+보험). 이게 빠지면 보험 라우팅에 구멍."""
    assert matches(query, pattern) is True


def test_prefix_compound_is_intentionally_not_matched():
    """'보험금'(보험+금)은 패턴 '보험'으로 안 잡힌다 — 의도된 비대칭.

    앞머리 합성어를 열려면 `startswith + 임의 꼬리`를 허용해야 하는데, 그러면
    '차이나타운'(차이+나타운)과 문자열로 구별할 수 없어 V6가 되살아난다.
    대신 프로필이 '보험금'을 패턴으로 명시한다(insurance-qa가 이미 그렇게 한다).
    """
    assert matches("보험금 청구 어떻게 해", "보험") is False
    assert matches("보험금 청구 어떻게 해", "보험금") is True


# --- 구(句) 패턴 ---


def test_phrase_pattern_uses_raw_substring():
    """공백이 든 패턴은 토큰으로 못 쪼갠다 — 구는 충분히 길어 오탐 위험이 낮다."""
    assert matches("할 일 목록 보여줘", "할 일") is True
    assert matches("다른 점이 뭐야", "다른 점") is True


# --- 최소 길이 게이트 ---


@pytest.mark.parametrize("pattern,valid", [
    ("건", False), ("끝", False), ("", False), (" ", False),
    ("보험", True), ("작업", True), ("할 일", True),
])
def test_min_length_gate(pattern, valid):
    assert is_valid_pattern(pattern) is valid


def test_min_length_is_two():
    assert MIN_PATTERN_LENGTH == 2


def test_one_char_pattern_never_matches():
    """게이트를 통과 못 한 패턴은 매칭도 하지 않는다 — 이중 방어."""
    assert matches("이 건 처리해줘", "건") is False


# --- tokenize ---


def test_tokenize_matches_ko_yaml_rule():
    """ko.yaml의 tokenize `[가-힣a-zA-Z]{2,}`와 같은 눈금."""
    assert tokenize("차이나타운 화재보험 있어요?") == ["차이나타운", "화재보험", "있어요"]


def test_tokenize_drops_numbers_and_single_chars():
    assert tokenize("1990-05-15 이 건") == []


def test_matches_any():
    assert matches_any("자동차보험 문의", ["사주", "보험"]) is True
    assert matches_any("차이나타운 가봤어", ["차이", "비교해"]) is False
