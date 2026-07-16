"""인용 계약 = 번호([n]) — 검증과 렌더링 (2026-07-16 재설계).

배경: 예전 계약은 "모델이 파일명을 텍스트로 쓴다"였고(ko.yaml), 가드는 그 문자열을
file_name과 대조했다. 인용 추출 정규식이 공백을 못 품어 파일명 꼬리만 잡히는데
그걸 전체 파일명과 완전일치로 비교한 탓에 **정확히 인용해도 100% 실패**했다
(이 코퍼스는 전 파일명이 공백 포함). 올바른 인용을 늘 환각으로 신고하면서 정작
조작 인용은 구별하지 못했다.

이제 프롬프트가 붙인 번호로 인용하고([1]), 가드는 범위만 본다(완전일치·오탐 불가).
사람에게 보일 때 build_response가 [n]→파일명으로 치환한다 — 모델이 긴 한글 파일명을
재현할 필요가 없어지는 게 요점이다.
"""

import pytest

from src.agent.nodes import render_citations
from src.safety.faithfulness import FaithfulnessGuard


# 프롬프트에 [1]..[3]으로 실린 청크 (순서 = 번호)
_PROMPT_DOCS = [
    {"file_name": "무배당 프로미라이프 New간편암건강보험2601 상품요약서.pdf"},
    {"file_name": "무배당 프로미라이프 New간편암건강보험2601 보험약관.pdf"},
    {"file_name": "무배당 프로미라이프 간편실손의료비보험(유병력자용)2604 보험약관.pdf"},
]


@pytest.fixture
def guard():
    return FaithfulnessGuard(router_llm=None)


# --- 가드: 번호 범위 검증 ---


def test_valid_index_passes(guard):
    assert guard._check_citations("최초 1회 지급합니다 [1]", _PROMPT_DOCS) is None


def test_multiple_valid_indices_pass(guard):
    assert guard._check_citations("보장은 다릅니다 [1][3]", _PROMPT_DOCS) is None


def test_boundary_index_passes(guard):
    assert guard._check_citations("마지막 문서 [3]", _PROMPT_DOCS) is None


def test_out_of_range_index_is_flagged(guard):
    """프롬프트에 3개만 실었는데 [7]을 쓰면 지어낸 것이다."""
    result = guard._check_citations("근거는 [7] 입니다", _PROMPT_DOCS)
    assert result is not None
    assert result.action == "warn"
    assert result.score == 0.5


def test_zero_index_is_flagged(guard):
    assert guard._check_citations("[0] 참고", _PROMPT_DOCS) is not None


def test_no_citation_returns_none(guard):
    assert guard._check_citations("암 진단비는 1회 지급됩니다.", _PROMPT_DOCS) is None


def test_filename_text_is_no_longer_judged(guard):
    """파일명을 텍스트로 써도 이제 판정 대상이 아니다 — 계약이 번호이기 때문.

    예전 구현이 여기서 오탐을 냈다(꼬리 vs 전체 파일명 완전일치 실패).
    """
    answer = "자세한 내용은 무배당 프로미라이프 New간편암건강보험2601 상품요약서.pdf 참고"
    assert guard._check_citations(answer, _PROMPT_DOCS) is None


# --- 렌더링: [n] → 파일명 ---


def test_render_replaces_index_with_filename():
    out = render_citations("최초 1회 지급합니다 [1]", _PROMPT_DOCS)
    assert "[출처: 무배당 프로미라이프 New간편암건강보험2601 상품요약서.pdf]" in out
    assert "[1]" not in out


def test_render_handles_multiple():
    out = render_citations("보장이 다릅니다 [1][3]", _PROMPT_DOCS)
    assert "상품요약서.pdf" in out
    assert "간편실손의료비보험(유병력자용)2604 보험약관.pdf" in out


def test_render_leaves_fabricated_index_untouched():
    """범위 밖 번호를 그럴듯한 파일명으로 바꿔주면 환각을 감춰주는 꼴이다.

    원문 그대로 두고 가드가 신고하게 한다.
    """
    out = render_citations("근거는 [7] 입니다", _PROMPT_DOCS)
    assert "[7]" in out


def test_render_without_docs_is_noop():
    assert render_citations("답변 [1]", []) == "답변 [1]"


def test_render_empty_answer():
    assert render_citations("", _PROMPT_DOCS) == ""


def test_render_falls_back_to_title():
    docs = [{"title": "제목만 있는 문서"}]
    assert "[출처: 제목만 있는 문서]" in render_citations("근거 [1]", docs)


def test_render_keeps_token_when_no_name():
    """이름이 없으면 번호를 그대로 둔다 — 빈 '[출처: ]'를 만들지 않는다."""
    assert render_citations("근거 [1]", [{}]) == "근거 [1]"
