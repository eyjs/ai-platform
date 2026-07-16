"""인용 검증 가드 — 오탐 제거 + 조작 인용 탐지 (2026-07-16 실측 회귀).

라이브 RAG 요청에서 발견: 답변이 소스에 **있는** 문서를 인용했는데 가드가
"참고 문서에 없습니다"로 신고했다. 원인은 완전일치 비교 —
인용 추출 정규식(`[\\w가-힣]+\\.pdf`)이 공백을 못 품어 파일명 꼬리만 잡는데
(`무배당 ... 상품요약서.pdf` → `상품요약서.pdf`), 그걸 전체 파일명과 완전일치로 봤다.
이 코퍼스는 전 파일명이 공백을 포함해 **정확히 인용해도 100% 실패**했다.

그 결과 이 가드는 올바른 인용을 늘 신고하면서 조작 인용은 구별 못 했고,
잘못된 faithfulness 점수(0.5)를 request_log에 남겼다.
"""

import pytest

from src.safety.faithfulness import FaithfulnessGuard


# 실제 코퍼스 파일명 (insurance-qa) — 전부 공백을 포함한다
_SOURCES = [
    {"file_name": "무배당 프로미라이프 New간편암건강보험2601 사업방법서.pdf"},
    {"file_name": "무배당 프로미라이프 New간편암건강보험2601 상품요약서.pdf"},
    {"file_name": "무배당 프로미라이프 New간편암건강보험2601 보험약관.pdf"},
    {"file_name": "무배당 프로미라이프 간편실손의료비보험(유병력자용)2604 보험약관.pdf"},
]


@pytest.fixture
def guard():
    return FaithfulnessGuard(router_llm=None)


# --- 오탐 (실측 재현) ---


def test_full_filename_citation_passes(guard):
    """답변이 전체 파일명을 정확히 인용하면 통과해야 한다 — 이게 실패하던 버그."""
    answer = "자세한 내용은 무배당 프로미라이프 New간편암건강보험2601 상품요약서.pdf 를 참고하세요."
    assert guard._check_citations(answer, _SOURCES) is None


def test_tail_only_citation_passes(guard):
    """정규식이 꼬리만 잡아도(공백 때문) 소스에 포함되면 통과."""
    answer = "보장 내용은 상품요약서.pdf 에 있습니다."
    assert guard._check_citations(answer, _SOURCES) is None


def test_multiple_valid_citations_pass(guard):
    answer = "상품요약서.pdf 와 보험약관.pdf 를 함께 확인하세요."
    assert guard._check_citations(answer, _SOURCES) is None


def test_abbreviated_citation_is_not_judged(guard):
    """확장자 없는 약칭("[출처: 상품요약서 섹션 ...]")은 검증 대상이 아니다.

    검증할 수 없는 것을 판정하지 않는다 — LLM의 정상 인용 습관이다.
    """
    answer = "암 진단비는 최초 1회 지급됩니다. [출처: 상품요약서 섹션 '(1) 보장의 종류']"
    assert guard._check_citations(answer, _SOURCES) is None


# --- 진짜 조작 인용 (반드시 잡는다) ---


def test_fabricated_document_is_flagged(guard):
    """소스에 없는 파일을 지어내면 잡아야 한다 — 이 검사의 존재 이유."""
    answer = "근거는 보험업법시행령.pdf 제3조입니다."
    result = guard._check_citations(answer, _SOURCES)
    assert result is not None
    assert result.action == "warn"
    assert result.score == 0.5


def test_fabricated_among_valid_is_flagged(guard):
    """유효 인용에 섞인 조작도 잡는다."""
    answer = "상품요약서.pdf 와 금융감독원고시.pdf 를 보세요."
    assert guard._check_citations(answer, _SOURCES) is not None


# --- 경계 ---


def test_no_citation_returns_none(guard):
    assert guard._check_citations("암 진단비는 1회 지급됩니다.", _SOURCES) is None


def test_case_insensitive_match(guard):
    assert guard._check_citations("상품요약서.PDF 참고", _SOURCES) is None


def test_empty_file_names_do_not_crash(guard):
    """file_name이 비거나 없는 소스가 섞여도 죽지 않는다."""
    docs = [{"file_name": ""}, {}, *_SOURCES]
    assert guard._check_citations("상품요약서.pdf 참고", docs) is None


def test_no_sources_flags_any_citation(guard):
    """소스가 없는데 파일을 인용하면 그건 지어낸 것이다."""
    assert guard._check_citations("상품요약서.pdf 참고", []) is not None
