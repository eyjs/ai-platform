"""HttpRerankerProvider graceful degrade 테스트.

HTTP 리랭커 실패 + 로컬 CrossEncoder 폴백 사용 불가(sentence_transformers 미설치)
상황에서 크래시 대신 입력 순서를 보존한 항등 랭킹으로 degrade 하는지 검증한다.
(운영 로그 617 재현: 이미지가 [local] extras 없이 빌드돼 폴백 생성이 ImportError.)
"""

import pytest


@pytest.mark.asyncio
async def test_degrades_to_identity_when_http_down_and_local_unavailable():
    from src.infrastructure.providers.reranking.http_reranker import HttpRerankerProvider

    # 도달 불가 URL + 존재하지 않는 폴백 모델 → 둘 다 실패
    r = HttpRerankerProvider("http://127.0.0.1:59999", fallback_model="nonexistent-model-xyz")
    docs = ["a", "b", "c", "d", "e", "f"]

    out = await r.rerank("q", docs, top_k=3)

    # 크래시 없이 top_k 만큼, 원본 순서 보존
    assert [o["index"] for o in out] == [0, 1, 2]
    # score 는 단조 감소 (소비자 정렬 후에도 원순서 유지 보장)
    scores = [o["score"] for o in out]
    assert scores == sorted(scores, reverse=True)
    # 폴백 불가 플래그가 세팅돼 이후 import 재시도를 하지 않음
    assert r._fallback_unavailable is True


@pytest.mark.asyncio
async def test_identity_respects_top_k_and_short_docs():
    from src.infrastructure.providers.reranking.http_reranker import HttpRerankerProvider

    r = HttpRerankerProvider("http://127.0.0.1:59999", fallback_model=None)
    # 폴백 모델이 없으면 곧장 항등 degrade
    out = await r.rerank("q", ["only-one"], top_k=10)
    assert [o["index"] for o in out] == [0]


@pytest.mark.asyncio
async def test_http_success_path_unaffected():
    """HTTP 정상 응답 시 degrade 경로를 타지 않고 정상 랭킹을 반환."""
    from unittest.mock import patch, AsyncMock
    from src.infrastructure.providers.reranking.http_reranker import HttpRerankerProvider

    r = HttpRerankerProvider("http://reranker.local", fallback_model="m")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            # 원본 index 2 가 최고 점수 → 재정렬돼야 함
            return [{"index": 0, "score": 0.1}, {"index": 1, "score": 0.2}, {"index": 2, "score": 5.0}]

    with patch.object(r._client, "post", new=AsyncMock(return_value=_Resp())):
        out = await r.rerank("q", ["a", "b", "c"], top_k=2)

    assert out[0]["index"] == 2  # 최고 점수 우선
    assert len(out) == 2
    assert r._fallback_unavailable is False


@pytest.mark.asyncio
async def test_http_rerank_sends_top_n():
    """[회귀] top_n 미전송 시 서버 기본값(10)이 후보 풀을 자른다 — 항상 명시."""
    from unittest.mock import AsyncMock, MagicMock
    from src.infrastructure.providers.reranking.http_reranker import HttpRerankerProvider

    provider = HttpRerankerProvider(base_url="http://reranker.test")
    captured = {}

    async def fake_post(url, json=None, **kwargs):
        captured.update(json or {})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=[{"index": 0, "score": 1.2}])
        return resp

    provider._client = MagicMock()
    provider._client.post = AsyncMock(side_effect=fake_post)

    await provider.rerank("질문", ["문서1", "문서2"], top_k=55)
    assert captured.get("top_n") == 55
