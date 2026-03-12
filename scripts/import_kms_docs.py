"""KMS 원본 문서를 ai-platform에 수집하는 스크립트.

사용법:
    python scripts/import_kms_docs.py [--api-url http://localhost:8010/api]
"""

import argparse
import os
import sys
from pathlib import Path

import httpx

KMS_STORAGE = Path(__file__).resolve().parents[1] / ".." / "KMS" / "packages" / "api" / "storage" / "originals"

DOCUMENTS = [
    {
        "file": "auto-terms.md",
        "title": "자동차보험 표준약관",
        "domain_code": "자동차보험",
    },
    {
        "file": "samsung-auto-summary.md",
        "title": "삼성화재 자동차보험 상품요약서",
        "domain_code": "자동차보험",
    },
    {
        "file": "auto-guide.md",
        "title": "자동차보험 가입안내서",
        "domain_code": "자동차보험",
    },
    {
        "file": "medical-terms.md",
        "title": "실손의료보험 표준약관",
        "domain_code": "실손보험",
    },
    {
        "file": "medical-coverage-guide.md",
        "title": "실손보험 보장가이드",
        "domain_code": "실손보험",
    },
    {
        "file": "insurance-law.md",
        "title": "보험업법",
        "domain_code": "보험법규",
    },
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8010/api")
    parser.add_argument("--count", type=int, default=0, help="수집할 문서 수 (0=전체)")
    args = parser.parse_args()

    storage_override = os.environ.get("KMS_STORAGE_PATH")
    storage = Path(storage_override) if storage_override else KMS_STORAGE
    if not storage.exists():
        print(f"Storage directory not found: {storage}")
        print("Set KMS_STORAGE_PATH environment variable to override.")
        sys.exit(1)

    docs = DOCUMENTS[:args.count] if args.count > 0 else DOCUMENTS

    print(f"API: {args.api_url}")
    print(f"Storage: {storage}")
    print(f"Documents: {len(docs)}")
    print()

    # health check
    try:
        r = httpx.get(f"{args.api_url}/health", timeout=5)
        print(f"Health: {r.json()}")
    except Exception as e:
        print(f"API unreachable: {e}")
        sys.exit(1)

    print()
    for doc in docs:
        file_path = storage / doc["file"]
        if not file_path.exists():
            print(f"SKIP {doc['file']}: file not found")
            continue

        content = file_path.read_text(encoding="utf-8")
        print(f"Ingesting: {doc['title']} ({len(content)} chars, domain={doc['domain_code']})")

        try:
            r = httpx.post(
                f"{args.api_url}/documents/ingest",
                json={
                    "title": doc["title"],
                    "content": content,
                    "domain_code": doc["domain_code"],
                    "file_name": doc["file"],
                    "security_level": "PUBLIC",
                },
                timeout=120,
            )
            if r.status_code == 200:
                result = r.json()
                print(f"  OK: doc_id={result['document_id']}, chunks={result['chunks']}")
            else:
                print(f"  ERROR {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"  ERROR: {e}")
        print()

    print("Done.")


if __name__ == "__main__":
    main()
