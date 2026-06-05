"""Step27: DB손해보험 적재 스크립트 단위 테스트 + 라이브 E2E(게이트).

단위(항상 실행):
- manifest 파싱 / payload 빌드 / external_id 멱등 키 / 텍스트 누락 skip
라이브 E2E(AIP_E2E_LIVE=1일 때만):
- 실제 적재 + 4개 검색 시나리오. 호스트 서비스(OCR:5052, 임베딩:8103) 의존.
  미설정 시 명확한 사유로 skip — 환경 의존을 조용히 통과시키지 않는다.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

# 스크립트를 모듈로 로드 (scripts/는 패키지가 아닐 수 있음)
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_db_damage_docs.py"
_spec = importlib.util.spec_from_file_location("import_db_damage_docs", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# ---- 픽스처 ----

def _sample_entry() -> dict:
    return {
        "doc_id": "10338_약관_99",
        "category": {"상품군": "자동차보험", "채널": "99", "소분류": "개인용"},
        "product_name": "개인용자동차보험(공동)",
        "product_code": "10338",
        "doc_type": "약관",
        "sale_start": "2026-03-01",
        "source_url": "https://www.idbins.com/x.pdf",
        "page_count": 69,
        "text_path": "text/sample.txt",
    }


# ---- 단위 테스트 (항상 실행) ----

def test_load_manifest_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        mod.load_manifest(tmp_path)
    assert "manifest.json" in str(exc.value)


def test_load_manifest_non_list_raises(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        mod.load_manifest(tmp_path)


def test_build_payload_has_idempotent_external_id():
    payload = mod.build_payload(_sample_entry(), "약관 본문")
    # external_id로 재적재 멱등성 보장 (Step25 UPSERT 연계)
    assert payload["external_id"] == "idbins:10338_약관_99"
    assert payload["domain_code"] == "DB-DAMAGE"


def test_build_payload_tags_search_metadata():
    payload = mod.build_payload(_sample_entry(), "본문")
    meta = payload["metadata"]
    # 4개 검색 시나리오(식별/메타데이터/관계/비교)에 필요한 태그
    assert meta["insurer"] == "DB손해보험"
    assert meta["product_name"] == "개인용자동차보험(공동)"
    assert meta["doc_type"] == "약관"
    assert meta["category_group"] == "자동차보험"


def test_build_payload_title_combines_product_and_type():
    payload = mod.build_payload(_sample_entry(), "본문")
    assert "개인용자동차보험(공동)" in payload["title"]
    assert "약관" in payload["title"]


def test_iter_documents_skips_missing_text(tmp_path, capsys):
    manifest = [_sample_entry()]  # text/sample.txt 없음
    docs = list(mod.iter_documents(manifest, tmp_path, limit=0))
    assert docs == []
    assert "SKIP" in capsys.readouterr().out


def test_iter_documents_yields_when_text_present(tmp_path):
    (tmp_path / "text").mkdir()
    (tmp_path / "text" / "sample.txt").write_text("약관 본문 내용", encoding="utf-8")
    manifest = [_sample_entry()]
    docs = list(mod.iter_documents(manifest, tmp_path, limit=0))
    assert len(docs) == 1
    entry, payload = docs[0]
    assert payload["content"] == "약관 본문 내용"


def test_iter_documents_respects_limit(tmp_path):
    (tmp_path / "text").mkdir()
    (tmp_path / "text" / "sample.txt").write_text("내용", encoding="utf-8")
    manifest = [dict(_sample_entry(), doc_id=f"d{i}") for i in range(5)]
    docs = list(mod.iter_documents(manifest, tmp_path, limit=2))
    assert len(docs) == 2


def test_iter_documents_skips_empty_text(tmp_path, capsys):
    (tmp_path / "text").mkdir()
    (tmp_path / "text" / "sample.txt").write_text("   \n  ", encoding="utf-8")
    docs = list(mod.iter_documents([_sample_entry()], tmp_path, limit=0))
    assert docs == []
    assert "빈 텍스트" in capsys.readouterr().out


# ---- 라이브 E2E (환경 의존 — 게이트) ----

_LIVE = os.environ.get("AIP_E2E_LIVE") == "1"
_SKIP_REASON = (
    "라이브 E2E는 호스트 서비스(OCR:5052, 임베딩:8103) + docforge/KMS 컨테이너 + "
    "API 서버 기동이 필요. 환경 준비 후 AIP_E2E_LIVE=1로 실행하세요. "
    "환경 의존을 조용히 통과시키지 않기 위해 명시적으로 skip합니다."
)


@pytest.mark.skipif(not _LIVE, reason=_SKIP_REASON)
class TestDbDamageLiveE2E:
    """DB손해보험 100건 적재 + 4개 검색 시나리오 (수동 검증용)."""

    API_URL = os.environ.get("AIP_API_URL", "http://localhost:8000/api")

    def test_ingest_100_docs(self):
        import httpx
        crawl_dir = Path(mod.DEFAULT_CRAWL_DIR)
        manifest = mod.load_manifest(crawl_dir)
        api_key = os.environ.get("AIP_API_KEY", "aip_dev_admin")
        ok = 0
        for _entry, payload in mod.iter_documents(manifest, crawl_dir, limit=0):
            r = httpx.post(
                f"{self.API_URL}/documents/ingest", json=payload,
                headers={"X-API-Key": api_key}, timeout=300,
            )
            assert r.status_code == 200, r.text
            ok += 1
        assert ok >= 1

    @pytest.mark.parametrize("scenario,query", [
        ("문서식별", "개인용자동차보험 약관 찾아줘"),
        ("메타데이터", "DB손해보험 자동차보험 상품 알려줘"),
        ("관계", "자동차보험 보장 항목 설명해줘"),
        ("비교", "개인용과 업무용 자동차보험 비교해줘"),
    ])
    def test_search_scenarios(self, scenario, query):
        import httpx
        api_key = os.environ.get("AIP_API_KEY", "aip_dev_admin")
        r = httpx.post(
            f"{self.API_URL}/chat",
            json={"question": query, "chatbot_id": "kms-assistant"},
            headers={"X-API-Key": api_key}, timeout=120,
        )
        assert r.status_code == 200, f"{scenario}: {r.text[:200]}"
        body = r.json()
        assert body.get("answer") or body.get("message"), f"{scenario}: 빈 응답"
