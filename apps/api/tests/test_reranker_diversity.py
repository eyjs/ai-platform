"""문서 다양성 캡 — 비교 질문에서 한 문서의 정원 독식 방지."""

from src.tools.internal.reranker_pipeline import _apply_document_diversity


def _mk(doc, fused):
    return {"data": {"document_id": doc}, "fused_score": fused,
            "rerank_score": fused, "vector_score": 0.5}


class TestDocumentDiversity:
    def test_single_doc_domination_broken(self):
        """문서 A가 상위 10을 독식해도 캡(10//3=3) 이후엔 B/C가 들어온다."""
        eligible = [_mk("A", 0.9 - i * 0.01) for i in range(10)]
        eligible += [_mk("B", 0.7), _mk("B", 0.69), _mk("C", 0.68)]
        eligible.sort(key=lambda x: -x["fused_score"])
        selected = eligible[:10]

        out = _apply_document_diversity(selected, eligible, top_k=10)
        docs = [o["data"]["document_id"] for o in out]
        assert len(out) == 10
        assert "B" in docs and "C" in docs  # 다른 문서가 반드시 포함
        assert docs.count("B") == 2 and docs.count("C") == 1

    def test_two_docs_split_by_cap_then_backfill(self):
        eligible = [_mk("A", 0.9 - i * 0.01) for i in range(6)]
        eligible += [_mk("B", 0.8 - i * 0.01) for i in range(6)]
        eligible.sort(key=lambda x: -x["fused_score"])
        out = _apply_document_diversity(eligible[:10], eligible, top_k=10)
        docs = [o["data"]["document_id"] for o in out]
        assert len(out) == 10
        assert set(docs) == {"A", "B"}
        assert docs.count("B") >= 3  # B도 캡만큼은 확보

    def test_underfill_backfills_from_overflow(self):
        """문서가 1종뿐이면 캡으로 줄이지 않고 백필해 정원을 유지한다."""
        eligible = [_mk("A", 0.9 - i * 0.01) for i in range(10)]
        out = _apply_document_diversity(eligible, eligible, top_k=10)
        assert len(out) == 10  # 총량 보존
