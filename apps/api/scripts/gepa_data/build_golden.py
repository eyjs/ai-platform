"""GEPA PoC — golden 평가셋 생성기 (결정론적, 합성 데이터).

[Addendum D — 데이터 출처 한정] saju_lookup은 외부 사주 백엔드에서 실데이터를
가져온다(src/tools/internal/saju_lookup.py). 오프라인에는 만세력 계산 엔진도, 외부
백엔드 접근도 없으므로 **실 사주 차트를 확보할 수 없다.** 따라서 본 생성기는
구조적으로 유효하고 다양한 **합성(synthetic) 차트**를 결정론적으로 만든다.
- 천간/지지/오행 어휘는 실제 명리 어휘를 사용 → 개인화 채점이 유의미하게 동작.
- 단, 사주 원국이 실제 생년월일에 대응하는 만세력 정확값은 아니다(합성).
metric은 형식·개인화만 측정하므로 PoC(배관 검증)에는 충분하다. 실 GEPA 운영
확장 시에는 외부 백엔드의 실데이터로 교체해야 한다(리포트에 명시).

사용:
  python scripts/gepa_data/build_golden.py            # saju_golden.json 생성
"""

from __future__ import annotations

import json
from pathlib import Path

# 천간(10) → 오행
HEAVENLY = ["갑", "을", "병", "정", "무", "기", "경", "신", "임", "계"]
HEAVENLY_ELEMENT = {
    "갑": "wood", "을": "wood", "병": "fire", "정": "fire", "무": "earth",
    "기": "earth", "경": "metal", "신": "metal", "임": "water", "계": "water",
}
EARTHLY = ["자", "축", "인", "묘", "진", "사", "오", "미", "신", "유", "술", "해"]
ELEMENTS = ["wood", "fire", "earth", "metal", "water"]
ELEMENT_KO = {"wood": "목", "fire": "화", "earth": "토", "metal": "금", "water": "수"}
SHINSAL_POOL = [
    "도화살", "역마살", "화개살", "천을귀인", "양인살",
    "백호살", "괴강살", "문창귀인", "월덕귀인", "홍염살",
]
STRATEGIES = ["수기 보강", "화기 보강", "목기 보강", "금기 강화", "토기 안정"]


def _energy(seed: int, dominant: str) -> dict:
    """우세 오행이 분명한 합성 오행 분포(합 ~100)."""
    base = {e: 8 + ((seed * (i + 3)) % 12) for i, e in enumerate(ELEMENTS)}
    base[dominant] += 30 + (seed % 15)  # 우세 오행 강조
    total = sum(base.values())
    norm = {e: round(v * 100 / total) for e, v in base.items()}
    strength = norm[dominant]
    status = "신강" if strength >= 30 else ("신약" if strength <= 18 else "중화")
    return {**norm, "selfStatus": status, "selfStrength": strength}


def _chart(i: int) -> dict:
    stem = HEAVENLY[i % 10]
    dominant = HEAVENLY_ELEMENT[stem]
    # 용신 = 분포상 가장 부족한 오행
    energy = _energy(i, dominant)
    deficient = min(ELEMENTS, key=lambda e: energy[e])
    shinsal = [SHINSAL_POOL[i % len(SHINSAL_POOL)], SHINSAL_POOL[(i * 3 + 1) % len(SHINSAL_POOL)]]
    shinsal = list(dict.fromkeys(shinsal))  # 중복 제거
    gender = "male" if i % 2 == 0 else "female"
    year = 1970 + (i % 40)
    month = 1 + (i % 12)
    day_n = 1 + (i % 27)
    return {
        "id": f"saju-{i:03d}",
        "saju_data": {
            "basic": {
                "name": f"내담자{i:03d}",
                "gender": gender,
                "birthDate": f"{year}-{month:02d}-{day_n:02d}",
                "fourPillars": {
                    "year": {"heavenlyStem": HEAVENLY[(i + 2) % 10], "earthlyBranch": EARTHLY[(i + 1) % 12]},
                    "month": {"heavenlyStem": HEAVENLY[(i + 5) % 10], "earthlyBranch": EARTHLY[(i + 7) % 12]},
                    "day": {"heavenlyStem": stem, "earthlyBranch": EARTHLY[i % 12]},
                    "hour": {"heavenlyStem": HEAVENLY[(i + 8) % 10], "earthlyBranch": EARTHLY[(i + 4) % 12]},
                },
            },
            "premium": {
                "interpretation": {
                    "energyScore": energy,
                    "yongsin": {
                        "yongsin": deficient,
                        "heesin": ELEMENTS[(ELEMENTS.index(deficient) + 1) % 5],
                        "strategy": STRATEGIES[i % len(STRATEGIES)],
                    },
                    "shinsal": shinsal,
                },
            },
        },
    }


def build(n_train: int = 50, n_val: int = 20) -> dict:
    charts = [_chart(i) for i in range(n_train + n_val)]
    return {
        "_meta": {
            "synthetic": True,
            "reason": "오프라인 — 외부 사주 백엔드/만세력 엔진 없음(Addendum D). 구조적 유효·합성.",
            "n_train": n_train,
            "n_val": n_val,
        },
        "default_section": "careerWealth",
        "train": charts[:n_train],
        "val": charts[n_train:],
    }


if __name__ == "__main__":
    out = Path(__file__).parent / "saju_golden.json"
    data = build()
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out} — train={len(data['train'])} val={len(data['val'])}")
