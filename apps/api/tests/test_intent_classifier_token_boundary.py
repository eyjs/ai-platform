"""IntentClassifier — 진단 V4/V6 오라우팅 시나리오 회귀.

진단서가 든 예시를 분류기 수준에서 그대로 재현한다:
- V4: "이 조건이 궁금해요" → 1글자 "건"에 걸려 TASK 오태깅 → STANDALONE 강제
- V6: "차이나타운 화재보험 있어요?" → "차이"에 걸려 CROSS_DOC_INTEGRATION 승격
"""

from unittest.mock import MagicMock

import pytest

from src.domain.agent_profile import IntentHint
from src.domain.execution_plan import QuestionType
from src.router.intent_classifier import IntentClassifier


def _classifier():
    return IntentClassifier(llm=None)


# --- V6: 비교 마커 ---


def test_v6_chinatown_is_not_a_comparison():
    """'차이나타운'이 '차이'로 읽혀 다문서 비교 전략이 붙던 오탐."""
    assert _classifier()._detect_comparison("차이나타운 화재보험 있어요?") is None


@pytest.mark.parametrize("query", [
    "두 상품 차이가 뭐야",
    "이 둘의 차이를 알려줘",
    "A랑 B 비교해줘",
    "실손보험이랑 암보험 다른 점이 뭐야",
    # "차이점"은 앞머리 합성어(차이+점)라 토큰 규칙으로는 "차이"에 안 걸린다.
    # ko.yaml comparison_markers에 명시해서 잡는다 — 엄격한 매칭의 대가이자,
    # 이 테스트가 그 명시가 사라지면 잡아낸다.
    "두 보험 상품 차이점 알려줘",
])
def test_v6_real_comparisons_still_detected(query):
    """오탐만 막고 진짜 비교를 놓치면 실사고(비교 질문 강등 → 한 상품 쏠림 오답)."""
    assert _classifier()._detect_comparison(query) == QuestionType.CROSS_DOC_INTEGRATION


# --- V4: 커스텀 인텐트 ---


_TASK_HINT = IntentHint(name="TASK", patterns=["태스크", "작업", "할 일", "업무", "건"], description="")
_INS_HINT = IntentHint(
    name="INSURANCE_INQUIRY", patterns=["보험", "보장", "보험료", "보험금"], description="",
)


@pytest.mark.parametrize("query", [
    "이 조건이 궁금해요",
    "물건 등록해줘",
    "건강검진 언제 받아",
    "안건 정리 좀",
])
def test_v4_one_char_pattern_no_longer_hijacks(query):
    """1글자 '건'이 조건·물건·건강·안건에 걸려 TASK로 오태깅되던 문제."""
    assert IntentClassifier._check_custom_intents(query, [_TASK_HINT]) is None


@pytest.mark.parametrize("query", [
    "작업 등록해줘",
    "작업을 완료했어",
    "태스크 목록 보여줘",
    "할 일 알려줘",
])
def test_v4_real_task_intents_still_matched(query):
    assert IntentClassifier._check_custom_intents(query, [_TASK_HINT]) == "TASK"


@pytest.mark.parametrize("query", [
    "자동차보험 대인배상 절차 알려줘",
    "화재보험 있어요?",
    "실손보험 보장 범위 알려줘",
    "보험금 청구 어떻게 해",
])
def test_v4_insurance_compounds_still_matched(query):
    """합성어(자동차+보험)를 놓치면 오탐을 고치면서 보험 라우팅에 구멍이 난다."""
    assert IntentClassifier._check_custom_intents(query, [_INS_HINT]) == "INSURANCE_INQUIRY"


def test_v4_first_matching_hint_wins_order_preserved():
    """궁합이 SAJU_ANALYSIS로 흡수되지 않도록 순서 의존(fortune-saju 주석)이 유지돼야 한다."""
    compat = IntentHint(name="COMPATIBILITY", patterns=["궁합", "결혼운"], description="")
    saju = IntentHint(name="SAJU_ANALYSIS", patterns=["사주", "운세"], description="")
    assert IntentClassifier._check_custom_intents("궁합 봐줘", [compat, saju]) == "COMPATIBILITY"
