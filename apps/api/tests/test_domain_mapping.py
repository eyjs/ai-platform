"""KMS 도메인 매핑 로더 + kms_sync 적용 테스트 (분류 불일치 근본해결).

검증:
  로더(resolve_product_domain):
    - 자동차보험 직결 / 장기보험·건강 / 장기보험·간병 매핑
    - 가장 구체적 경로 우선(장기보험/건강 > 장기보험)
    - 미매핑(장기보험/종합) → None(fallback 신호)
    - categoryPath 빈 배열/도메인코드만 → None
    - 매핑 미정의 도메인(HANHWA) → None
  kms_sync.sync_document:
    - 매핑 적중 시 product_domain 으로 ingest(domain_code 치환) — chunks 도 동일
    - 미매핑/부재 시 회사도메인 fallback + kms_sync_domain_unmapped WARN(조용한 누락 0)
"""

from __future__ import annotations

import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import src.services.domain_mapping as dm
from src.services.kms_sync import UNPLACED_DOMAIN, KmsSyncService


# ---- 테스트용 매핑 YAML 픽스처 (실 seed 와 격리) ----

_MAPPING_YAML = textwrap.dedent(
    """
    DB-DAMAGE:
      "자동차보험": 자동차보험
      "장기보험/건강": 건강보험
      "장기보험/간병": 간병보험
      "일반보험/화재": 화재보험
    """
).strip()


@pytest.fixture
def mapping_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """임시 매핑 파일을 가리키게 하고 캐시를 리로드한다(테스트 격리)."""
    path = tmp_path / "domain_mapping.yaml"
    path.write_text(_MAPPING_YAML, encoding="utf-8")
    monkeypatch.setenv("AIP_DOMAIN_MAPPING_PATH", str(path))
    dm.reload_mapping()
    yield
    monkeypatch.delenv("AIP_DOMAIN_MAPPING_PATH", raising=False)
    dm.reload_mapping()


# ---- 로더 단위 테스트 ----

class TestResolveProductDomain:
    def test_자동차보험_직결(self, mapping_env):
        assert dm.resolve_product_domain("DB-DAMAGE", ["DB-DAMAGE", "자동차보험", "개인용"]) == "자동차보험"

    def test_자동차보험_업무용도_같은_상품도메인(self, mapping_env):
        assert dm.resolve_product_domain("DB-DAMAGE", ["DB-DAMAGE", "자동차보험", "업무용"]) == "자동차보험"

    def test_장기보험_건강(self, mapping_env):
        assert dm.resolve_product_domain("DB-DAMAGE", ["DB-DAMAGE", "장기보험", "건강"]) == "건강보험"

    def test_장기보험_간병(self, mapping_env):
        assert dm.resolve_product_domain("DB-DAMAGE", ["DB-DAMAGE", "장기보험", "간병"]) == "간병보험"

    def test_일반보험_화재(self, mapping_env):
        assert dm.resolve_product_domain("DB-DAMAGE", ["DB-DAMAGE", "일반보험", "화재"]) == "화재보험"

    def test_가장_구체적_경로_우선(self, mapping_env):
        # 더 깊은 경로가 와도 장기보험/건강 으로 매칭(하위 약관까지 내려가도 적중).
        assert (
            dm.resolve_product_domain("DB-DAMAGE", ["DB-DAMAGE", "장기보험", "건강", "약관"]) == "건강보험"
        )

    def test_미매핑_카테고리_None_fallback(self, mapping_env):
        # 장기보험/종합 은 매핑 미정의 → None(회사도메인 fallback 신호).
        assert dm.resolve_product_domain("DB-DAMAGE", ["DB-DAMAGE", "장기보험", "종합"]) is None

    def test_장기보험_top만으로는_None(self, mapping_env):
        # top 카테고리만으로는 매핑 키(장기보험/...)가 없으므로 None.
        assert dm.resolve_product_domain("DB-DAMAGE", ["DB-DAMAGE", "장기보험"]) is None

    def test_categoryPath_빈배열_None(self, mapping_env):
        assert dm.resolve_product_domain("DB-DAMAGE", []) is None

    def test_도메인코드만_있으면_None(self, mapping_env):
        # categoryPath[0] 은 도메인코드이므로 제외 → 카테고리 없음 → None.
        assert dm.resolve_product_domain("DB-DAMAGE", ["DB-DAMAGE"]) is None

    def test_매핑_미정의_도메인_None(self, mapping_env):
        # HANHWA 등은 매핑에 없음 → None(그대로 적재 + WARN).
        assert dm.resolve_product_domain("HANHWA", ["HANHWA", "자동차보험", "개인용"]) is None

    def test_빈_도메인_None(self, mapping_env):
        assert dm.resolve_product_domain("", ["", "자동차보험"]) is None


_MAPPING_YAML_WITH_DEFAULT = textwrap.dedent(
    """
    D02:
      "자동차보험": 자동차보험
      "_default": _common
    """
).strip()


@pytest.fixture
def mapping_env_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "domain_mapping.yaml"
    path.write_text(_MAPPING_YAML_WITH_DEFAULT, encoding="utf-8")
    monkeypatch.setenv("AIP_DOMAIN_MAPPING_PATH", str(path))
    dm.reload_mapping()
    yield
    monkeypatch.delenv("AIP_DOMAIN_MAPPING_PATH", raising=False)
    dm.reload_mapping()


class TestDefaultRule:
    """도메인 단위 `_default` 폴백 (실사고: KMS 가 categoryPath=[코드]만 보내는 회귀 대응)."""

    def test_코드만_와도_default_착지(self, mapping_env_default):
        # 실사고 재현: categoryPath=["D02"] (카테고리 없음) → _common 착지(회사코드 태깅 방지).
        assert dm.resolve_product_domain("D02", ["D02"]) == "_common"

    def test_빈_categoryPath_default_착지(self, mapping_env_default):
        assert dm.resolve_product_domain("D02", []) == "_common"

    def test_미매핑_카테고리도_default_착지(self, mapping_env_default):
        assert dm.resolve_product_domain("D02", ["D02", "장기보험", "종합"]) == "_common"

    def test_명시_매핑이_default_보다_우선(self, mapping_env_default):
        assert dm.resolve_product_domain("D02", ["D02", "자동차보험", "개인용"]) == "자동차보험"

    def test_default_없는_도메인은_기존과_동일_None(self, mapping_env):
        # 기존 픽스처(_default 미정의) — 하위호환: None(회사도메인 fallback + WARN).
        assert dm.resolve_product_domain("DB-DAMAGE", ["DB-DAMAGE"]) is None


# ---- kms_sync.sync_document 적용 테스트 ----

def _make_service() -> KmsSyncService:
    settings = type(
        "S",
        (),
        {
            "kms_api_url": "http://kms.local",
            "kms_internal_key": "key",
            "default_tenant_id": "default",
            "docforge_url": "http://docforge.local",
            "docforge_internal_key": "dfkey",
        },
    )()
    vector_store = AsyncMock()
    pipeline = AsyncMock()
    pipeline.ingest_text = AsyncMock(return_value={"document_id": None, "chunks": 0})
    svc = KmsSyncService(settings, vector_store, pipeline)  # type: ignore[arg-type]
    # 네트워크 의존 메서드를 스텁 — 매핑 분기까지만 검증.
    svc._fetch_document_meta = AsyncMock(  # type: ignore[method-assign]
        return_value={"fileName": "약관.pdf", "fileType": "pdf", "securityLevel": "PUBLIC"}
    )
    svc._download_file = AsyncMock(return_value=b"%PDF-1.4 stub")  # type: ignore[method-assign]
    svc._delete_by_external_id = AsyncMock(return_value=0)  # type: ignore[method-assign]
    svc._set_external_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    # 미적재 상태(fresh ingest)로 고정 — 매핑 분기만 검증. 재태깅/스킵 분기는 별도 테스트.
    svc._get_existing = AsyncMock(return_value=None)  # type: ignore[method-assign]
    # advisory lock 은 no-op 으로 스텁 (mock pool 로 실 SQL 실행 방지).
    @asynccontextmanager
    async def _noop_lock(_document_id: str):
        yield
    svc._doc_lock = _noop_lock  # type: ignore[method-assign]
    return svc


class TestSyncDocumentMapping:
    @pytest.mark.asyncio
    async def test_매핑_적중시_상품도메인으로_ingest(self, mapping_env):
        svc = _make_service()
        data = {
            "domainCodes": ["DB-DAMAGE"],
            "categoryPath": ["DB-DAMAGE", "자동차보험", "개인용"],
        }

        await svc.sync_document("doc-1", data)

        svc._pipeline.ingest_text.assert_awaited_once()
        kwargs = svc._pipeline.ingest_text.await_args.kwargs
        # 회사도메인(DB-DAMAGE)이 아니라 상품도메인(자동차보험)으로 적재 — chunks 도 동일 값.
        assert kwargs["domain_code"] == "자동차보험"

    @pytest.mark.asyncio
    async def test_장기보험_건강_매핑(self, mapping_env):
        svc = _make_service()
        data = {
            "domainCodes": ["DB-DAMAGE"],
            "categoryPath": ["DB-DAMAGE", "장기보험", "건강"],
        }

        await svc.sync_document("doc-2", data)

        assert svc._pipeline.ingest_text.await_args.kwargs["domain_code"] == "건강보험"

    @pytest.mark.asyncio
    async def test_미매핑_카테고리_회사도메인_fallback_및_WARN(self, mapping_env, caplog):
        svc = _make_service()
        data = {
            "domainCodes": ["DB-DAMAGE"],
            "categoryPath": ["DB-DAMAGE", "장기보험", "종합"],  # 미매핑
        }

        with caplog.at_level("WARNING"):
            await svc.sync_document("doc-3", data)

        # fallback: 회사도메인 그대로 적재 (조용한 누락 0 — WARN 가시화).
        assert svc._pipeline.ingest_text.await_args.kwargs["domain_code"] == "DB-DAMAGE"
        assert any("kms_sync_domain_unmapped" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_categoryPath_부재시_회사도메인_fallback_및_WARN(self, mapping_env, caplog):
        # 구 KMS/타 consumer: categoryPath 없음 → 하위호환(도메인 그대로) + WARN.
        svc = _make_service()
        data = {"domainCodes": ["DB-DAMAGE"]}  # categoryPath 없음

        with caplog.at_level("WARNING"):
            await svc.sync_document("doc-4", data)

        assert svc._pipeline.ingest_text.await_args.kwargs["domain_code"] == "DB-DAMAGE"
        assert any("kms_sync_domain_unmapped" in r.getMessage() for r in caplog.records)


# ---- 임베딩·배치 분리 설계 테스트 ----


class TestEmbedPlacementSeparation:
    """업로드 즉시 임베딩(holding) + 배치 시 재태깅 분기."""

    @pytest.mark.asyncio
    async def test_배치전_created_holding_도메인으로_임베딩(self, mapping_env):
        # domainCodes 없는 업로드(document.created) → __unplaced__ 로 즉시 적재
        svc = _make_service()
        data: dict = {}  # 배치 전: 도메인 없음

        await svc.sync_document("doc-new", data, event="document.created")

        svc._pipeline.ingest_text.assert_awaited_once()
        assert svc._pipeline.ingest_text.await_args.kwargs["domain_code"] == UNPLACED_DOMAIN

    @pytest.mark.asyncio
    async def test_배치시_재임베딩없이_재태깅(self, mapping_env):
        # 이미 holding 으로 적재된 문서 + 배치(document.updated) → 재태깅만, ingest X
        svc = _make_service()
        svc._get_existing = AsyncMock(  # type: ignore[method-assign]
            return_value={"id": "aip-1", "domain_code": UNPLACED_DOMAIN, "security_level": "PUBLIC", "chunk_count": 10}
        )
        svc._retag_and_refresh = AsyncMock(return_value=3)  # type: ignore[method-assign]
        data = {"domainCodes": ["DB-DAMAGE"], "categoryPath": ["DB-DAMAGE", "자동차보험", "개인용"]}

        result = await svc.sync_document("doc-1", data, event="document.updated")

        # doc_id(재사용) + 상품도메인 + 보안등급 으로 재태깅. ingest 는 호출 안 됨.
        svc._retag_and_refresh.assert_awaited_once_with("aip-1", "자동차보험", "PUBLIC")
        svc._pipeline.ingest_text.assert_not_awaited()
        assert result["status"] == "retagged"
        assert result["domain_code"] == "자동차보험"

    @pytest.mark.asyncio
    async def test_보안등급_변경은_재임베딩없이_청크까지_전파(self, mapping_env):
        # 동일 도메인이라도 securityLevel 변경 시 재태깅으로 청크까지 전파(다운그레이드 누출 방지)
        svc = _make_service()
        svc._fetch_document_meta = AsyncMock(  # type: ignore[method-assign]
            return_value={"fileName": "약관.pdf", "fileType": "pdf", "securityLevel": "CONFIDENTIAL"}
        )
        svc._get_existing = AsyncMock(  # type: ignore[method-assign]
            return_value={"id": "aip-1", "domain_code": "자동차보험", "security_level": "PUBLIC", "chunk_count": 10}
        )
        svc._retag_and_refresh = AsyncMock(return_value=5)  # type: ignore[method-assign]
        data = {"domainCodes": ["DB-DAMAGE"], "categoryPath": ["DB-DAMAGE", "자동차보험", "개인용"]}

        result = await svc.sync_document("doc-1", data, event="document.updated")

        svc._retag_and_refresh.assert_awaited_once_with("aip-1", "자동차보험", "CONFIDENTIAL")
        svc._pipeline.ingest_text.assert_not_awaited()
        assert result["status"] == "retagged"

    @pytest.mark.asyncio
    async def test_변경없는_메타이벤트는_스킵(self, mapping_env):
        # 이미 배치된 문서 + 도메인·보안등급 모두 불변 → 스킵(재임베딩·재태깅 X, 멱등)
        svc = _make_service()
        svc._get_existing = AsyncMock(  # type: ignore[method-assign]
            return_value={"id": "aip-1", "domain_code": "자동차보험", "security_level": "PUBLIC", "chunk_count": 10}
        )
        svc._retag_and_refresh = AsyncMock(return_value=0)  # type: ignore[method-assign]
        data = {"domainCodes": ["DB-DAMAGE"], "categoryPath": ["DB-DAMAGE", "자동차보험", "개인용"]}

        result = await svc.sync_document("doc-1", data, event="document.updated")

        assert result["status"] == "skipped"
        svc._pipeline.ingest_text.assert_not_awaited()
        svc._retag_and_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_파일재업로드는_기존도메인_보존하며_재적재(self, mapping_env):
        # 이미 적재된 문서 + file_uploaded(콘텐츠 변경) → 재적재, 기존 도메인 보존
        svc = _make_service()
        svc._get_existing = AsyncMock(  # type: ignore[method-assign]
            return_value={"id": "aip-1", "domain_code": "자동차보험", "security_level": "PUBLIC", "chunk_count": 10}
        )
        data: dict = {}  # 재업로드 이벤트엔 도메인 없음 → 기존값 보존

        await svc.sync_document("doc-1", data, event="document.file_uploaded")

        svc._pipeline.ingest_text.assert_awaited_once()
        assert svc._pipeline.ingest_text.await_args.kwargs["domain_code"] == "자동차보험"
