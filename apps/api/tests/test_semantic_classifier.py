"""SemanticClassifier 테스트 — fast-path / LLM 의미분류 / NONE·저신뢰·환각 차단."""

from src.router.semantic_classifier import Candidate, SemanticClassifier


class _StubLLM:
    """generate_json이 고정 응답을 반환하는 스텁. 호출 횟수 기록."""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = 0

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        self.calls += 1
        return self.response


CANDS = [Candidate("연애·인연"), Candidate("돈·재물"), Candidate("일·진로")]


async def test_fastpath_exact_skips_llm():
    """정확 라벨(버튼 탭)은 LLM 호출 없이 즉시 매칭."""
    llm = _StubLLM({"label": "돈·재물", "confidence": 0.9})
    clf = SemanticClassifier(llm)
    r = await clf.classify("돈·재물", CANDS)
    assert r.label == "돈·재물"
    assert llm.calls == 0


async def test_llm_classifies_freetext():
    """자유입력은 LLM 의미분류로 후보에 매핑."""
    llm = _StubLLM({"label": "돈·재물", "confidence": 0.85})
    clf = SemanticClassifier(llm)
    r = await clf.classify("요즘 돈 들어올 구석이 없어 답답해", CANDS)
    assert r.label == "돈·재물"
    assert llm.calls == 1


async def test_llm_none_returns_none():
    llm = _StubLLM({"label": "NONE", "confidence": 0.1})
    clf = SemanticClassifier(llm)
    assert (await clf.classify("오늘 날씨 좋네", CANDS)).label is None


async def test_low_confidence_rejected():
    """threshold(0.6) 미만은 채택 안 함."""
    llm = _StubLLM({"label": "돈·재물", "confidence": 0.3})
    clf = SemanticClassifier(llm)
    assert (await clf.classify("모호한 말", CANDS)).label is None


async def test_hallucinated_label_rejected():
    """후보에 없는 라벨은 신뢰도 높아도 차단."""
    llm = _StubLLM({"label": "없는옵션", "confidence": 0.99})
    clf = SemanticClassifier(llm)
    assert (await clf.classify("x", CANDS)).label is None


async def test_no_llm_fastpath_only():
    """LLM 미주입 시 fast-path만 — 자유입력은 None, 정확 라벨은 매칭(하위호환)."""
    clf = SemanticClassifier(None)
    assert (await clf.classify("자유로운 문장", CANDS)).label is None
    assert (await clf.classify("일·진로", CANDS)).label == "일·진로"


async def test_empty_candidates():
    clf = SemanticClassifier(_StubLLM({"label": "x", "confidence": 1.0}))
    assert (await clf.classify("뭐든", [])).label is None
