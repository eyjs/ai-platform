"""hanja_normalizer 단위 테스트 — 사주 출력 한자 차단."""

from __future__ import annotations

import re

import pytest

from src.tools.internal.hanja_normalizer import normalize_llm_text, to_hangul

_CJK = re.compile(r"[㐀-䶿一-鿿]")


class TestToHangul:
    def test_십성_괄호_글로스_제거(self):
        assert to_hangul("정재(正財)가 2개, 편재(偏財)가 1개") == "정재가 2개, 편재가 1개"

    def test_천간지지_원국_변환(self):
        assert to_hangul("己巳 일주, 丙寅 월주, 戊辰 시주") == "기사 일주, 병인 월주, 무진 시주"

    def test_오행_변환(self):
        assert to_hangul("오행 木火土金水 중 土가 많다") == "오행 목화토금수 중 토가 많다"

    def test_미등록_한자는_제거(self):
        # 매핑에 없는 CJK 한자는 strip → 한자 노출 0 보장
        assert "什" not in to_hangul("什么 테스트")
        assert "테스트" in to_hangul("什么 테스트")

    def test_십성_단독글자(self):
        assert to_hangul("比肩과 劫財") == "비견과 겁재"

    def test_한글만_있으면_불변(self):
        s = "너는 안정형 재물운이야. 토가 많아."
        assert to_hangul(s) == s

    def test_빈문자열_None_안전(self):
        assert to_hangul("") == ""
        assert to_hangul(None) is None  # type: ignore[arg-type]

    @pytest.mark.parametrize("text", [
        "정재(正財) 편재(偏財) 정관(正官) 편관(偏官)",
        "甲乙丙丁戊己庚辛壬癸 子丑寅卯辰巳午未申酉戌亥",
        "木火土金水 陰陽 桃花 驛馬",
    ])
    def test_변환후_잔여_한자_없음(self, text):
        assert _CJK.search(to_hangul(text)) is None


class TestNormalizeLlmText:
    def test_dict_재귀_변환(self):
        out = normalize_llm_text({"summary": "정재(正財)형", "advice": "金 보강해"})
        assert out == {"summary": "정재형", "advice": "금 보강해"}

    def test_중첩_list_dict(self):
        out = normalize_llm_text({"a": ["木", {"b": "正財"}]})
        assert out == {"a": ["목", {"b": "정재"}]}

    def test_비문자열은_그대로(self):
        out = normalize_llm_text({"n": 42, "ok": True, "s": "土"})
        assert out == {"n": 42, "ok": True, "s": "토"}
