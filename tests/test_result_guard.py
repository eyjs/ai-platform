"""결과 가드 단위 테스트."""


def _chunk(content: str) -> dict:
    return {"chunk_id": "c1", "score": 0.9, "content": content}


def test_empty_input():
    from src.tools.internal.result_guard import guard_results
    assert guard_results([]) == []


def test_no_pii_passes_through():
    from src.tools.internal.result_guard import guard_results
    chunks = [_chunk("보험약관 제1조 목적")]
    result = guard_results(chunks)
    assert result[0]["content"] == "보험약관 제1조 목적"


def test_masks_resident_number():
    from src.tools.internal.result_guard import guard_results
    chunks = [_chunk("주민번호: 900101-1234567 입니다")]
    result = guard_results(chunks)
    assert "[주민번호]" in result[0]["content"]
    assert "900101-1234567" not in result[0]["content"]


def test_masks_phone_number():
    from src.tools.internal.result_guard import guard_results
    chunks = [_chunk("연락처 010-1234-5678로 문의")]
    result = guard_results(chunks)
    assert "[전화번호]" in result[0]["content"]
    assert "010-1234-5678" not in result[0]["content"]


def test_masks_account_number():
    from src.tools.internal.result_guard import guard_results
    chunks = [_chunk("계좌 110-123-456789")]
    result = guard_results(chunks)
    assert "[계좌번호]" in result[0]["content"]
    assert "110-123-456789" not in result[0]["content"]


def test_masks_multiple_pii():
    from src.tools.internal.result_guard import guard_results
    chunks = [_chunk("홍길동 900101-1234567 전화 010-9999-8888")]
    result = guard_results(chunks)
    assert "[주민번호]" in result[0]["content"]
    assert "[전화번호]" in result[0]["content"]


def test_preserves_other_fields():
    from src.tools.internal.result_guard import guard_results
    chunks = [{"chunk_id": "c1", "score": 0.9, "content": "900101-1234567", "file_name": "doc.pdf"}]
    result = guard_results(chunks)
    assert result[0]["chunk_id"] == "c1"
    assert result[0]["score"] == 0.9
    assert result[0]["file_name"] == "doc.pdf"
