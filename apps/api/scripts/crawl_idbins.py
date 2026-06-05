"""DB손해보험 상품공시(idbins.com) 기초서류 크롤러 — 테스트 데이터 수집.

공개 공시문서(보험약관/사업방법서/상품요약서)를 4단계 AJAX 체인으로 수집해
PDF 다운로드 → pymupdf 텍스트 파싱 → manifest.json 생성한다.

AJAX 체인:
  Step2 /insuPcPbanFindProductStep2_AX.do  (arc_knd_lgcg_nm, sl_chn_nm, arc_knd_mdcg_nm) -> 상품목록
  Step3 /insuPcPbanFindProductStep3_AX.do  (pdc_nm)                                       -> 판매기간(SQNO)
  Step4 /insuPcPbanFindProductStep4_AX.do  (sqno)                                         -> 문서 파일명 3종
  PDF   GET /cYakgwanDown.do?FilePath=InsProduct/<파일명>

수집 설계: 카테고리별 여러 상품(비교 검증용) + 상품별 약관/사업방법서/상품요약서 3종
(상호참조 검증용)을 함께 모아 ~100개 테스트 문서를 만든다.

사용법:
    python scripts/crawl_idbins.py --limit 100
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import quote

import fitz  # pymupdf
import requests

BASE = "https://www.idbins.com"
LIST_PAGE = f"{BASE}/FWMAIV1534.do"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# (상품군, 채널, 소분류) — 비교 검증을 위해 카테고리별 형제 상품이 모이도록 구성
CATEGORIES = [
    ("자동차보험", "99", "개인용"),
    ("자동차보험", "99", "업무용"),
    ("자동차보험", "99", "영업용"),
    ("장기보험", "Off-Line", "건강"),
    ("장기보험", "Off-Line", "상해"),
    ("장기보험", "Off-Line", "운전자"),
    ("장기보험", "Off-Line", "질병"),
    ("일반", "99", "상해"),
    ("일반", "99", "화재"),
    ("일반", "99", "종합"),
]

# 문서타입 ← Step4 응답 필드명
DOC_FIELDS = {
    "약관": "INPL_FINM",
    "사업방법서": "BIZ_MDDC_FINM",
    "상품요약서": "CNSL_SMAR_FINM",
}

DELAY = 0.4  # 서버 부하 배려 (초)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Referer": LIST_PAGE,
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    s.get(LIST_PAGE, timeout=20)  # JSESSIONID 획득
    return s


def ajax(s: requests.Session, path: str, payload: dict) -> list[dict]:
    r = s.post(
        f"{BASE}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=UTF-8"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("result", [])


def fmt_date(v: str | None) -> str | None:
    if v and len(v) == 8:
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}"
    return None


def crawl(limit: int, out_dir: Path) -> None:
    pdf_dir = out_dir / "pdfs"
    txt_dir = out_dir / "text"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    txt_dir.mkdir(parents=True, exist_ok=True)

    s = make_session()
    manifest: list[dict] = []
    seen_files: set[str] = set()

    print(f"목표: {limit}개 문서\n")

    for lgcg, chn, mdcg in CATEGORIES:
        if len(manifest) >= limit:
            break
        try:
            products = ajax(
                s,
                "/insuPcPbanFindProductStep2_AX.do",
                {"arc_knd_lgcg_nm": lgcg, "sl_chn_nm": chn, "arc_knd_mdcg_nm": mdcg, "arc_pdc_sl_yn": "1"},
            )
        except Exception as e:
            print(f"[SKIP] 카테고리 {lgcg}/{mdcg} 상품목록 실패: {e}")
            continue
        time.sleep(DELAY)
        print(f"■ {lgcg} / {chn} / {mdcg} — 상품 {len(products)}개")

        for p in products:
            if len(manifest) >= limit:
                break
            pdc_nm = p["PDC_NM"]
            try:
                periods = ajax(
                    s, "/insuPcPbanFindProductStep3_AX.do",
                    {"pdc_nm": pdc_nm, "arc_pdc_sl_yn": "1"},
                )
            except Exception as e:
                print(f"  [SKIP] {pdc_nm} 기간조회 실패: {e}")
                continue
            time.sleep(DELAY)
            periods = [x for x in periods if x.get("SL_STR_DT", "")[:8].isdigit() and len(x["SL_STR_DT"]) == 8]
            if not periods:
                continue
            # 최신 판매기간 1건
            latest = max(periods, key=lambda x: x["SL_STR_DT"])
            sqno = latest["SQNO"]
            try:
                docs = ajax(
                    s, "/insuPcPbanFindProductStep4_AX.do",
                    {"sqno": sqno, "arc_pdc_sl_yn": "1"},
                )
            except Exception as e:
                print(f"  [SKIP] {pdc_nm} 문서조회 실패: {e}")
                continue
            time.sleep(DELAY)
            if not docs:
                continue
            doc = docs[0]

            for doc_type, field in DOC_FIELDS.items():
                if len(manifest) >= limit:
                    break
                fname = doc.get(field)
                if not fname or fname in seen_files:
                    continue
                seen_files.add(fname)

                # PDF 다운로드
                try:
                    url = f"{BASE}/cYakgwanDown.do?FilePath=InsProduct/{quote(fname)}"
                    pr = s.get(url, timeout=90)
                    if pr.status_code != 200 or not pr.content.startswith(b"%PDF"):
                        print(f"    [WARN] {doc_type} 다운로드 실패 ({pr.status_code}): {fname}")
                        continue
                except Exception as e:
                    print(f"    [WARN] {doc_type} 다운로드 예외: {e}")
                    continue
                time.sleep(DELAY)

                safe = fname.replace("/", "_")
                pdf_path = pdf_dir / safe
                pdf_path.write_bytes(pr.content)

                # 파싱 (pymupdf)
                try:
                    with fitz.open(stream=pr.content, filetype="pdf") as pdf:
                        pages = pdf.page_count
                        text = "\n".join(page.get_text() for page in pdf)
                except Exception as e:
                    print(f"    [WARN] {doc_type} 파싱 실패: {e}")
                    continue

                txt_path = txt_dir / (safe.rsplit(".", 1)[0] + ".txt")
                txt_path.write_text(text, encoding="utf-8")

                # 파일명 파싱: 상품코드_날짜_타입_상품명.pdf
                parts = fname.rsplit(".", 1)[0].split("_")
                product_code = parts[0] if parts else None

                entry = {
                    "doc_id": f"{product_code}_{doc_type}_{sqno}",
                    "category": {"상품군": lgcg, "채널": chn, "소분류": mdcg},
                    "product_name": pdc_nm,
                    "product_code": product_code,
                    "doc_type": doc_type,
                    "sale_start": fmt_date(latest.get("SL_STR_DT")),
                    "sale_end": fmt_date(latest.get("SL_FIN_DT")),
                    "sqno": sqno,
                    "pdf_filename": fname,
                    "source_url": url,
                    "page_count": pages,
                    "char_count": len(text),
                    "pdf_path": str(pdf_path.relative_to(out_dir)),
                    "text_path": str(txt_path.relative_to(out_dir)),
                }
                manifest.append(entry)
                print(f"    [{len(manifest):3d}] {doc_type:5s} {pdc_nm} — {pages}p, {len(text):,}자")

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 요약
    print(f"\n=== 완료: {len(manifest)}개 문서 ===")
    by_cat: dict[str, int] = {}
    by_type: dict[str, int] = {}
    products: set[str] = set()
    for m in manifest:
        by_cat[m["category"]["상품군"]] = by_cat.get(m["category"]["상품군"], 0) + 1
        by_type[m["doc_type"]] = by_type.get(m["doc_type"], 0) + 1
        products.add(m["product_name"])
    print(f"상품군별: {by_cat}")
    print(f"문서타입별: {by_type}")
    print(f"고유 상품 수: {len(products)}")
    print(f"manifest: {out_dir / 'manifest.json'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "crawl_data"))
    args = ap.parse_args()
    crawl(args.limit, Path(args.out))


if __name__ == "__main__":
    main()
