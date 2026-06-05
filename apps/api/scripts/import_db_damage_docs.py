"""DB손해보험 idbins.com 크롤 데이터(crawl_data/)를 ai-platform에 적재하는 스크립트 (Step27).

manifest.json + text/*.txt를 읽어 DB-DAMAGE 도메인으로 /documents/ingest 한다.
external_id="idbins:{doc_id}"로 멱등 적재(재실행해도 중복 행 없음 — Step25 UPSERT 활용).

crawl_data/는 gitignore — 스크립트만 커밋, 데이터는 로컬에서 crawl_idbins.py로 수집.

사용법:
    python scripts/import_db_damage_docs.py --api-url http://localhost:8000/api
    python scripts/import_db_damage_docs.py --limit 10           # 소형 테스트
    python scripts/import_db_damage_docs.py --dry-run            # API 호출 없이 검증만

환경:
    AIP_API_KEY     ingest 인증 키 (기본 aip_dev_admin)
    호스트 서비스(OCR :5052, 임베딩 :8103) + docforge/KMS 컨테이너 기동 필요.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

DOMAIN_CODE = "DB-DAMAGE"
DEFAULT_CRAWL_DIR = Path(__file__).resolve().parent / "crawl_data"


def load_manifest(crawl_dir: Path) -> list[dict]:
    """manifest.json을 로드한다. 없으면 명확한 에러."""
    manifest_path = crawl_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"manifest.json 없음: {manifest_path}. "
            f"먼저 'python scripts/crawl_idbins.py --limit 100'으로 수집하세요."
        )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"manifest.json은 list 형식이어야 합니다. got: {type(data).__name__}")
    return data


def build_payload(entry: dict, content: str) -> dict:
    """manifest 엔트리 + 텍스트 → ingest 요청 페이로드.

    external_id로 멱등성 보장, metadata에 보험사/상품/카테고리 등 검색 메타 태깅.
    """
    category = entry.get("category", {})
    return {
        "title": f"{entry.get('product_name', '')} {entry.get('doc_type', '')}".strip(),
        "content": content,
        "domain_code": DOMAIN_CODE,
        "file_name": Path(entry.get("text_path", entry["doc_id"])).name,
        "security_level": "PUBLIC",
        "external_id": f"idbins:{entry['doc_id']}",
        "source_url": entry.get("source_url"),
        "metadata": {
            "insurer": "DB손해보험",
            "product_name": entry.get("product_name"),
            "product_code": entry.get("product_code"),
            "doc_type": entry.get("doc_type"),
            "category_group": category.get("상품군"),
            "category_sub": category.get("소분류"),
            "sale_start": entry.get("sale_start"),
            "page_count": entry.get("page_count"),
            "source": "idbins_crawl",
        },
    }


def iter_documents(manifest: list[dict], crawl_dir: Path, limit: int):
    """manifest를 순회하며 (entry, payload)를 산출. 텍스트 파일 없으면 skip(경고)."""
    count = 0
    for entry in manifest:
        if limit and count >= limit:
            return
        text_path = crawl_dir / entry.get("text_path", "")
        if not text_path.exists():
            print(f"  SKIP {entry['doc_id']}: 텍스트 파일 없음 ({text_path})")
            continue
        content = text_path.read_text(encoding="utf-8")
        if not content.strip():
            print(f"  SKIP {entry['doc_id']}: 빈 텍스트")
            continue
        yield entry, build_payload(entry, content)
        count += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-url", default="http://localhost:8000/api")
    ap.add_argument("--crawl-dir", default=str(DEFAULT_CRAWL_DIR))
    ap.add_argument("--limit", type=int, default=0, help="적재 문서 수 (0=전체)")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 페이로드 검증만")
    args = ap.parse_args()

    crawl_dir = Path(args.crawl_dir)
    try:
        manifest = load_manifest(crawl_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"manifest: {len(manifest)}건, domain={DOMAIN_CODE}, dry_run={args.dry_run}")

    if args.dry_run:
        n = 0
        for entry, payload in iter_documents(manifest, crawl_dir, args.limit):
            n += 1
            print(f"  [{n:3d}] {payload['external_id']} | {payload['title']} | {len(payload['content']):,}자")
        print(f"\nDRY-RUN 완료: {n}건 적재 대상 (API 호출 없음)")
        return 0

    api_key = os.environ.get("AIP_API_KEY", "aip_dev_admin")
    headers = {"X-API-Key": api_key}

    try:
        r = httpx.get(f"{args.api_url}/health", timeout=5)
        print(f"Health: {r.status_code}")
    except Exception as e:
        print(f"ERROR: API 접속 불가 {args.api_url}: {e}", file=sys.stderr)
        print("호스트 서비스(OCR:5052/임베딩:8103) + API 서버 기동을 확인하세요.", file=sys.stderr)
        return 2

    ok, fail = 0, 0
    for entry, payload in iter_documents(manifest, crawl_dir, args.limit):
        try:
            r = httpx.post(
                f"{args.api_url}/documents/ingest", json=payload, headers=headers, timeout=300,
            )
            if r.status_code == 200:
                body = r.json()
                print(f"  OK {payload['external_id']}: {body}")
                ok += 1
            else:
                print(f"  ERROR {r.status_code} {payload['external_id']}: {r.text[:200]}")
                fail += 1
        except Exception as e:
            print(f"  ERROR {payload['external_id']}: {e}")
            fail += 1

    print(f"\n적재 완료: 성공 {ok}, 실패 {fail}")
    return 0 if fail == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
