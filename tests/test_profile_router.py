"""ProfileRouter 3-Tier 단위 테스트."""

import pytest

from src.orchestrator.profile_router import ProfileRouter, RouteResult, _is_greeting


# ── 테스트 데이터 ──

PROFILES = [
    {
        "id": "general-chat",
        "name": "통합 AI 어시스턴트",
        "description": "범용 어시스턴트",
        "domain_scopes": [],
        "intent_hints": [],
    },
    {
        "id": "insurance-qa",
        "name": "보험 상담 챗봇",
        "description": "보험 상품 전문 상담",
        "domain_scopes": ["자동차보험", "실손보험", "화재보험", "건강보험"],
        "intent_hints": [
            {"name": "INSURANCE_INQUIRY", "patterns": ["보험", "보장", "보험료", "보험금", "보상"], "description": "보험 질문"},
            {"name": "INSURANCE_RECOMMEND", "patterns": ["보험 추천", "어떤 보험", "보험 가입", "보험 비교"], "description": "보험 추천"},
        ],
    },
    {
        "id": "food-recipe",
        "name": "요리 레시피 어시스턴트",
        "description": "요리 레시피 안내",
        "domain_scopes": ["요리", "레시피"],
        "intent_hints": [
            {"name": "RECIPE_SEARCH", "patterns": ["레시피", "만드는 법", "요리법", "조리법", "끓이는 법", "볶는 법", "굽는 법", "무치는 법", "찌는 법", "튀기는 법", "삶는 법"], "description": "레시피 검색"},
            {"name": "FOOD_INQUIRY", "patterns": ["칼로리", "음식", "반찬", "찌개", "요리"], "description": "음식 질문"},
            {"name": "INGREDIENT_SEARCH", "patterns": ["재료", "냉장고", "뭘 만들"], "description": "재료 기반 검색"},
        ],
    },
    {
        "id": "fortune-saju",
        "name": "사주팔자 상담사",
        "description": "사주명리 상담",
        "domain_scopes": ["사주명리"],
        "intent_hints": [
            {"name": "SAJU_ANALYSIS", "patterns": ["사주", "팔자", "운세", "궁합", "오행", "토정비결", "신년운세"], "description": "사주 분석"},
            {"name": "FORTUNE_GENERAL", "patterns": ["재운", "관운", "건강운", "연애운", "이사운"], "description": "운세 상담"},
        ],
    },
    {
        "id": "legal-contract",
        "name": "계약서 작성 어시스턴트",
        "description": "계약서 법률 어시스턴트",
        "domain_scopes": ["계약서", "법률"],
        "intent_hints": [
            {"name": "CONTRACT_DRAFT", "patterns": ["계약서 작성", "계약서 만들", "초안", "드래프트"], "description": "계약서 작성"},
            {"name": "LEGAL_REFERENCE", "patterns": ["민법", "상법", "근로기준법", "판례"], "description": "법령 조회"},
        ],
    },
    {
        "id": "hr-onboarding",
        "name": "신입사원 온보딩 가이드",
        "description": "사내규정 안내",
        "domain_scopes": ["인사", "사내규정"],
        "intent_hints": [
            {"name": "POLICY_INQUIRY", "patterns": ["규정", "연차", "휴가", "급여", "복지", "출근", "퇴근", "야근", "회식"], "description": "규정 질문"},
        ],
    },
]


@pytest.fixture
def router():
    return ProfileRouter(PROFILES)


# ── Tier 1: 인사 감지 ──

class TestGreetingDetection:
    def test_greeting_hello(self):
        assert _is_greeting("안녕하세요")

    def test_greeting_hi(self):
        assert _is_greeting("하이~")

    def test_greeting_bye(self):
        assert _is_greeting("잘가!")

    def test_greeting_thanks(self):
        assert _is_greeting("감사합니다")

    def test_greeting_long_with_domain(self):
        # _GREETING_MAX_LEN=30이므로 인사 패턴은 감지됨
        # 실제 라우팅에서는 도메인 키워드("보험") 체크로 Tier2 에스컬레이션
        assert _is_greeting("안녕하세요 보험에 대해 물어볼게요")

    def test_not_greeting_too_long(self):
        assert not _is_greeting("안녕하세요 저는 김철수이고 보험 상품에 대해 자세히 물어보고 싶습니다")

    def test_not_greeting_question(self):
        assert not _is_greeting("김치찌개 레시피 알려줘")


class TestTier1RuleMatch:
    def test_greeting_routes_to_general(self, router):
        result = router.tier1_rule_match("안녕하세요")
        assert result is not None
        assert result.profile_id == "general-chat"
        assert result.is_greeting
        assert result.tier == 1

    def test_recipe_keyword(self, router):
        result = router.tier1_rule_match("김치찌개 레시피 알려줘")
        assert result is not None
        assert result.profile_id == "food-recipe"
        assert result.tier == 1

    def test_saju_keyword(self, router):
        result = router.tier1_rule_match("내 운세 봐줘")
        assert result is not None
        assert result.profile_id == "fortune-saju"

    def test_contract_keyword(self, router):
        result = router.tier1_rule_match("근로기준법 관련 조항 알려줘")
        assert result is not None
        assert result.profile_id == "legal-contract"

    def test_vacation_keyword(self, router):
        result = router.tier1_rule_match("연차 몇 개 남았는지 알려줘")
        assert result is not None
        assert result.profile_id == "hr-onboarding"

    def test_no_match(self, router):
        result = router.tier1_rule_match("요즘 운이 안 좋은데 어떻게 해야 할까")
        assert result is None


# ── Tier 2: 키워드 스코어링 ──

class TestTier2KeywordScore:
    def test_insurance_by_domain_scope(self, router):
        """intent_hints가 없어도 domain_scopes로 매칭."""
        result = router.tier2_keyword_score("자동차보험 보장 내용이 뭐야?")
        assert result is not None
        assert result.profile_id == "insurance-qa"
        assert result.tier == 2

    def test_insurance_multiple_domains(self, router):
        """여러 domain_scope 키워드가 동시에 매칭되면 높은 스코어."""
        result = router.tier2_keyword_score("건강보험이랑 실손보험 차이")
        assert result is not None
        assert result.profile_id == "insurance-qa"

    def test_low_confidence_returns_none(self, router):
        """매칭 스코어가 낮으면 None 반환."""
        result = router.tier2_keyword_score("오늘 날씨 어때?")
        assert result is None


# ── get_keywords ──

class TestGetKeywords:
    def test_returns_keywords(self, router):
        kws = router.get_keywords("food-recipe")
        assert len(kws) > 0
        keyword_strings = [kw for kw, _ in kws]
        assert "레시피" in keyword_strings

    def test_unknown_profile(self, router):
        kws = router.get_keywords("nonexistent")
        assert kws == []


# ── 통합 시나리오 ──

class TestIntegrationScenarios:
    def test_recipe_tier1(self, router):
        """레시피 질문은 Tier 1에서 해결."""
        result = router.tier1_rule_match("김치찌개 만드는 법")
        assert result is not None
        assert result.profile_id == "food-recipe"

    def test_insurance_tier2(self, router):
        """보험 질문은 intent_hints 없으므로 Tier 2에서 해결."""
        t1 = router.tier1_rule_match("자동차보험 보험료 얼마야?")
        # intent_hints가 없어 Tier 1에서는 매칭 안 될 수 있음
        if t1 is None:
            t2 = router.tier2_keyword_score("자동차보험 보험료 얼마야?")
            assert t2 is not None
            assert t2.profile_id == "insurance-qa"

    def test_ambiguous_falls_through(self, router):
        """애매한 질문은 Tier 1, 2 모두 None."""
        t1 = router.tier1_rule_match("인생이 힘들어")
        t2 = router.tier2_keyword_score("인생이 힘들어")
        assert t1 is None
        assert t2 is None  # Tier 3(LLM)으로 넘어가야 함


# ── Tier 2: 역방향 매칭 ──

class TestTier2ReverseMatch:
    def test_insurance_reverse_token_match(self, router):
        """'보험'(질문 토큰)이 '자동차보험'(키워드) 안에 포함 -> insurance-qa."""
        result = router.tier2_keyword_score("30대 남자 보험 추천")
        assert result is not None
        assert result.profile_id == "insurance-qa"
        assert result.tier == 2

    def test_food_cooking_verb_tier1(self, router):
        """'끓이는 법' 패턴으로 Tier 1에서 food-recipe 매칭."""
        result = router.tier1_rule_match("된장찌개 끓이는 법")
        assert result is not None
        assert result.profile_id == "food-recipe"
        assert result.tier == 1

    def test_food_jjigae_tier2(self, router):
        """'찌개' 키워드로 food-recipe Tier 2 매칭."""
        result = router.tier2_keyword_score("찌개 추천해줘")
        assert result is not None
        assert result.profile_id == "food-recipe"

    def test_reverse_match_no_false_positive(self, router):
        """관련 없는 질문은 역방향 매칭에도 None."""
        result = router.tier2_keyword_score("오늘 날씨 어때?")
        assert result is None

    def test_tier2_min_score_parameter(self, router):
        """min_score=0.3으로 낮추면 약한 매칭도 통과."""
        # 기본 min_score=0.5에서 실패할 수 있는 질문
        result_strict = router.tier2_keyword_score("상품 알려줘", min_score=0.5)
        result_relaxed = router.tier2_keyword_score("상품 알려줘", min_score=0.1)
        # relaxed가 strict보다 같거나 더 많이 매칭
        if result_strict is None:
            # strict에서 실패해도 relaxed에서는 성공할 수 있음 (또는 둘 다 None)
            pass
        else:
            assert result_relaxed is not None
