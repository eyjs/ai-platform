"""LocaleBundle 단위 테스트.

로케일 번들 로더 검증:
- YAML 로드 + 싱글턴 관리
- 프롬프트/메시지/라벨 접근
- regex 패턴 pre-compile
- PII 패턴
- 대명사 패턴 (dict list)
- 숫자 패턴
- 팩토리 메서드
"""

import re

import pytest

from src.locale.bundle import LocaleBundle, get_locale, set_locale


# --- 테스트 데이터 ---


def _sample_data() -> dict:
    return {
        "prompts": {
            "greeting": "안녕하세요, {name}님!",
            "system": "반드시 한국어로 답변하세요.",
        },
        "messages": {
            "error": "오류가 발생했습니다: {reason}",
            "simple": "완료되었습니다.",
        },
        "labels": {
            "user": "사용자",
            "assistant": "어시스턴트",
        },
        "patterns": {
            "greeting": [
                "^(안녕|하이|헬로)",
                "(감사합니다|고마워)",
            ],
            "tokenize": "[가-힣a-zA-Z]{2,}",
            "pronoun": [
                {"pattern": "^(그|이|저)(것|거)\\s", "confidence": 0.9},
                {"pattern": "^(더|또)\\s*(알려|설명)", "confidence": 0.8},
            ],
            "escape_keywords": ["취소", "나가기", "exit"],
        },
        "pii_patterns": [
            {"pattern": "\\d{6}-\\d{7}", "label": "주민등록번호"},
            {"pattern": "\\d{3}-\\d{4}-\\d{4}", "label": "전화번호"},
        ],
        "pii_result_guard": [
            {"pattern": "\\d{6}-[1-4]\\d{6}", "replacement": "[주민번호]"},
        ],
        "validators": {
            "phone": "^01[016789]-\\d{3,4}-\\d{4}$",
        },
        "number_patterns": [
            "\\d{1,3}(?:,\\d{3})+",
            "\\d+(?:\\.\\d+)?%",
        ],
        "citation_extensions": ["pdf", "md", "xlsx"],
        "validation_hints": {
            "phone": "올바른 전화번호 형식이 아닙니다.",
        },
    }


@pytest.fixture
def bundle():
    return LocaleBundle(_data=_sample_data())


# --- 싱글턴 관리 ---


class TestSingleton:

    def test_get_locale_before_init_raises(self):
        """초기화 전 호출 시 RuntimeError."""
        import src.locale.bundle as mod
        original = mod._bundle
        mod._bundle = None
        try:
            with pytest.raises(RuntimeError, match="초기화"):
                get_locale()
        finally:
            mod._bundle = original

    def test_set_and_get_locale(self, bundle):
        import src.locale.bundle as mod
        original = mod._bundle
        try:
            set_locale(bundle)
            assert get_locale() is bundle
        finally:
            mod._bundle = original


# --- 문자열 접근자 ---


class TestStringAccessors:

    def test_prompt_simple(self, bundle):
        assert bundle.prompt("system") == "반드시 한국어로 답변하세요."

    def test_prompt_with_format(self, bundle):
        result = bundle.prompt("greeting", name="홍길동")
        assert result == "안녕하세요, 홍길동님!"

    def test_prompt_missing_key(self, bundle):
        assert bundle.prompt("nonexistent") == ""

    def test_message_simple(self, bundle):
        assert bundle.message("simple") == "완료되었습니다."

    def test_message_with_format(self, bundle):
        result = bundle.message("error", reason="타임아웃")
        assert result == "오류가 발생했습니다: 타임아웃"

    def test_message_missing_key(self, bundle):
        assert bundle.message("nonexistent") == ""

    def test_label(self, bundle):
        assert bundle.label("user") == "사용자"
        assert bundle.label("assistant") == "어시스턴트"

    def test_label_missing_key(self, bundle):
        assert bundle.label("nonexistent") == ""


# --- 패턴 접근자 ---


class TestPatternAccessors:

    def test_raw_patterns_list(self, bundle):
        patterns = bundle.raw_patterns("greeting")
        assert len(patterns) == 2
        assert patterns[0] == "^(안녕|하이|헬로)"

    def test_raw_patterns_string(self, bundle):
        patterns = bundle.raw_patterns("tokenize")
        assert patterns == ["[가-힣a-zA-Z]{2,}"]

    def test_raw_patterns_missing(self, bundle):
        assert bundle.raw_patterns("nonexistent") == []

    def test_compiled_patterns_list(self, bundle):
        patterns = bundle.compiled_patterns("greeting")
        assert len(patterns) == 2
        assert all(isinstance(p, re.Pattern) for p in patterns)
        assert patterns[0].search("안녕하세요")

    def test_compiled_pattern_single(self, bundle):
        pattern = bundle.compiled_pattern("tokenize")
        assert isinstance(pattern, re.Pattern)
        assert pattern.findall("안녕하세요 hello")

    def test_compiled_pattern_missing(self, bundle):
        assert bundle.compiled_pattern("nonexistent") is None

    def test_pattern_set(self, bundle):
        keywords = bundle.pattern_set("escape_keywords")
        assert keywords == {"취소", "나가기", "exit"}

    def test_pattern_set_missing(self, bundle):
        assert bundle.pattern_set("nonexistent") == set()


# --- 대명사 패턴 ---


class TestPronounPatterns:

    def test_pronoun_patterns_loaded(self, bundle):
        patterns = bundle.pronoun_patterns()
        assert len(patterns) == 2

    def test_pronoun_is_tuple_of_pattern_and_confidence(self, bundle):
        patterns = bundle.pronoun_patterns()
        regex, confidence = patterns[0]
        assert isinstance(regex, re.Pattern)
        assert confidence == 0.9

    def test_pronoun_matches(self, bundle):
        patterns = bundle.pronoun_patterns()
        regex, _ = patterns[0]
        assert regex.search("그것 알려줘")

    def test_pronoun_second_pattern(self, bundle):
        patterns = bundle.pronoun_patterns()
        regex, confidence = patterns[1]
        assert confidence == 0.8
        assert regex.search("더 알려줘")


# --- PII 패턴 ---


class TestPIIPatterns:

    def test_pii_patterns_loaded(self, bundle):
        assert len(bundle.pii_patterns) == 2

    def test_pii_pattern_detects_rrn(self, bundle):
        regex, label = bundle.pii_patterns[0]
        assert label == "주민등록번호"
        assert regex.search("123456-1234567")

    def test_pii_pattern_detects_phone(self, bundle):
        regex, label = bundle.pii_patterns[1]
        assert label == "전화번호"
        assert regex.search("010-1234-5678")

    def test_pii_result_guard(self, bundle):
        assert len(bundle.pii_result_guard) == 1
        regex, replacement = bundle.pii_result_guard[0]
        assert replacement == "[주민번호]"
        result = regex.sub(replacement, "번호: 901231-1234567")
        assert "[주민번호]" in result


# --- 특화 접근자 ---


class TestSpecialAccessors:

    def test_validators(self, bundle):
        assert "phone" in bundle.validators
        assert isinstance(bundle.validators["phone"], re.Pattern)
        assert bundle.validators["phone"].match("010-1234-5678")

    def test_number_patterns(self, bundle):
        assert len(bundle.number_patterns) == 2
        assert bundle.number_patterns[0].search("1,000,000")
        assert bundle.number_patterns[1].search("3.5%")

    def test_citation_extensions(self, bundle):
        assert bundle.citation_extensions == ["pdf", "md", "xlsx"]

    def test_validation_hint(self, bundle):
        assert "전화번호" in bundle.validation_hint("phone")
        assert bundle.validation_hint("nonexistent") == ""

    def test_key_count(self, bundle):
        assert bundle.key_count > 0


# --- 팩토리 ---


class TestFactory:

    def test_load_ko_yaml(self):
        """실제 ko.yaml 로드 검증."""
        bundle = LocaleBundle.load("src/locale/ko.yaml")
        assert bundle.prompt("llm_system_prefix") != ""
        assert bundle.key_count > 10
        assert len(bundle.pii_patterns) > 0
        assert len(bundle.pronoun_patterns()) > 0

    def test_load_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            LocaleBundle.load("src/locale/nonexistent.yaml")
