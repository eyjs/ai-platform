"""노이즈 필터 단위 테스트."""


def _chunk(chunk_id: str, score: float) -> dict:
    return {"chunk_id": chunk_id, "score": score, "content": f"content-{chunk_id}"}


def test_empty_input():
    from src.tools.internal.noise_filter import filter_noise
    assert filter_noise([]) == []


def test_under_min_keep_returns_all():
    from src.tools.internal.noise_filter import filter_noise
    candidates = [_chunk(str(i), 0.01 - i * 0.005) for i in range(3)]
    result = filter_noise(candidates)
    assert len(result) == 3


def test_gap_filter_cuts_at_30_percent():
    from src.tools.internal.noise_filter import filter_noise
    candidates = [
        _chunk("a", 0.016),
        _chunk("b", 0.015),
        _chunk("c", 0.014),
        _chunk("d", 0.013),
        _chunk("e", 0.012),
        _chunk("f", 0.009),
        _chunk("g", 0.005),
    ]
    result = filter_noise(candidates)
    assert len(result) == 5
    assert result[-1]["chunk_id"] == "e"


def test_min_keep_overrides_gap():
    from src.tools.internal.noise_filter import filter_noise
    candidates = [
        _chunk("a", 0.020),
        _chunk("b", 0.019),
        _chunk("c", 0.010),
        _chunk("d", 0.009),
        _chunk("e", 0.008),
        _chunk("f", 0.002),
    ]
    result = filter_noise(candidates)
    assert len(result) >= 5


def test_all_same_score():
    from src.tools.internal.noise_filter import filter_noise
    candidates = [_chunk(str(i), 0.01) for i in range(10)]
    result = filter_noise(candidates)
    assert len(result) == 10


def test_zero_top_score():
    from src.tools.internal.noise_filter import filter_noise
    candidates = [_chunk(str(i), 0.0) for i in range(8)]
    result = filter_noise(candidates)
    assert len(result) == 5
