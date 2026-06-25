"""GEPA 프롬프트 최적화 PoC — 오프라인 CLI (사주 리포트 섹션).

[Addendum A] 이 PoC가 증명하는 것은 "우리 환경에서 GEPA 파이프라인이 오프라인
end-to-end로 돈다"는 **배관 검증**뿐이다. metric이 향상돼도 "GEPA가 품질을 올린다"고
결론내지 않는다(사주 품질은 주관적, metric은 형식 기반). 품질은 사람 블라인드로만.

[Addendum C] 기본은 --dry-run(baseline만). 사람이 "metric이 합리적으로 채점하는지"
확인·승인하기 전까지 --apply-run(실제 GEPA 진화) 금지.

실행(apps/api 디렉토리, 프로젝트 venv):
  python scripts/gepa_optimize.py --section careerWealth --dry-run --limit 12
  python scripts/gepa_optimize.py --section careerWealth --apply-run --max-metric-calls 60
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

# 사이드카 모듈(gepa_metrics, gepa_adapters)을 import 가능하게 — scripts/ 를 path에.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from src.config import settings  # noqa: E402

import gepa_adapters as ga  # noqa: E402
from gepa_metrics import LENGTH_BANDS  # noqa: E402
from src.tools.internal.saju_report_paper import _extract_json  # noqa: E402


def _now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_golden(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / max(1, len(xs)), 4)


def _print_samples(adapter, batch, eval_batch, k: int) -> None:
    print(f"\n=== 샘플 출력 {min(k, len(batch))}건 (사람이 metric 합리성 확인용) ===")
    for i in range(min(k, len(batch))):
        out = eval_batch.outputs[i]
        bd = out["breakdown"]
        print(f"\n--- [{out['id']}] score={eval_batch.scores[i]:.3f} ---")
        print("성분:", {k2: round(v, 2) for k2, v in bd.items()})
        print("토큰:", batch[i]["tokens"])
        resp = out["response"].strip().replace("\n", " ")
        print("응답:", (resp[:400] + " …") if len(resp) > 400 else resp)
        print("피드백:", out["feedback"][:300])


def run_baseline(args) -> dict:
    golden = _load_golden(Path(args.evalset))
    section = args.section or golden.get("default_section", "careerWealth")
    val = golden["val"]
    if args.limit:
        val = val[: args.limit]

    adapter = ga.SajuSectionAdapter(section, concurrency=args.concurrency, target_provider=args.target_llm)
    seed = {ga.COMPONENT: ga.seed_system_prompt(section)}
    batch = [ga.build_datainst(item, section) for item in val]

    target_model = adapter._target_label
    print(f"[baseline] section={section} val={len(batch)} target={target_model}")
    print(f"[baseline] seed prompt 길이={len(seed[ga.COMPONENT])}자, "
          f"길이밴드={LENGTH_BANDS}")

    eval_batch = adapter.evaluate(batch, seed, capture_traces=True)
    mean = _mean(eval_batch.scores)

    # per-example 표 (Addendum G)
    per_example = [
        {"id": o["id"], "score": s, "breakdown": {k: round(v, 3) for k, v in o["breakdown"].items()}}
        for o, s in zip(eval_batch.outputs, eval_batch.scores)
    ]
    print(f"\n[baseline] valset 평균 score = {mean}  (n={len(batch)})")
    print("[baseline] per-example:", [(p["id"], p["score"]) for p in per_example])
    _print_samples(adapter, batch, eval_batch, args.samples)

    report = {
        "phase": "baseline (gate C — STOP)",
        "scope": "배관 검증만 — 품질 결론 아님 (Addendum A)",
        "section": section,
        "target_model": target_model,
        "provider_mode": settings.provider_mode.value,
        "synthetic_eval": golden.get("_meta", {}),
        "val_n": len(batch),
        "baseline_mean_score": mean,
        "per_example": per_example,
        "length_bands": LENGTH_BANDS,
        "samples": [
            {
                "id": o["id"],
                "tokens": b["tokens"],
                "response": o["response"],
                "feedback": o["feedback"],
                "breakdown": o["breakdown"],
            }
            for b, o in list(zip(batch, eval_batch.outputs))[: args.samples]
        ],
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"baseline_{section}_{_now_tag()}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[baseline] 리포트 저장: {out_path}")
    print("\n>>> gate C STOP: metric이 합리적으로 채점하는지 사람이 확인 후 --apply-run 승인 필요.")
    return report


def run_apply(args) -> dict:
    # [fail-loud] Anthropic(유료)을 명시적으로 고른 경우에만 키 검증. 기본(로컬)은 불필요.
    uses_anthropic = args.target_llm in ("main", "commercial") or args.reflection_llm in ("main", "commercial")
    if uses_anthropic and not settings.anthropic_api_key:
        raise SystemExit(
            "AIP_ANTHROPIC_API_KEY 미설정인데 main/commercial(유료)을 선택함 → 로컬 강등. "
            "GEPA 진화를 거부합니다(fail-loud). 비용0으로 돌리려면 --target-llm 9b --reflection-llm 14b."
        )
    import gepa  # 지연 import — dry-run 경로는 gepa 미설치여도 동작.

    golden = _load_golden(Path(args.evalset))
    section = args.section or golden.get("default_section", "careerWealth")
    adapter = ga.SajuSectionAdapter(section, concurrency=args.concurrency, target_provider=args.target_llm)
    seed = {ga.COMPONENT: ga.seed_system_prompt(section)}
    trainset = [ga.build_datainst(it, section) for it in golden["train"]]
    valset = [ga.build_datainst(it, section) for it in golden["val"]]

    def _quality_mean(ev) -> float:
        return _mean([o["quality"] for o in ev.outputs])

    base_eval = adapter.evaluate(valset, seed, capture_traces=False)
    before_obj = _mean(base_eval.scores)
    before_q = _quality_mean(base_eval)
    print(f"[apply] target={adapter._target_label} reflection={ga.provider_label(args.reflection_llm)}")
    print(f"[apply] before(val) objective={before_obj} quality={before_q}  "
          f"— GEPA 시작 (max_metric_calls={args.max_metric_calls}, "
          f"size_penalty K={ga.SIZE_PENALTY_K} deadband={ga.SIZE_PENALTY_DEADBAND})")

    result = gepa.optimize(
        seed_candidate=seed,
        trainset=trainset,
        valset=valset,
        adapter=adapter,
        reflection_lm=ga.make_reflection_lm(args.reflection_llm),
        max_metric_calls=args.max_metric_calls,
        display_progress_bar=True,
    )
    best = result.best_candidate
    after_eval = adapter.evaluate(valset, best, capture_traces=False)
    after_obj = _mean(after_eval.scores)
    after_q = _quality_mean(after_eval)

    # 크기 게이트(게이트1: ≤ seed×1.5) 판정
    seed_len = len(seed[ga.COMPONENT])
    cand_len = len(best.get(ga.COMPONENT, ""))
    ratio = round(cand_len / max(1, seed_len), 3)
    size_gate_pass = cand_len <= seed_len * 1.5

    print(f"[apply] after(val)  objective={after_obj} quality={after_q}  "
          f"(Δobj={after_obj - before_obj:+.4f}, Δquality={after_q - before_q:+.4f})")
    print(f"[apply] candidate {cand_len}자 / seed {seed_len}자 = {ratio}x — "
          f"게이트1(≤1.5x): {'PASS' if size_gate_pass else 'FAIL'}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = _now_tag()
    (out_dir / f"candidate_{section}_{tag}.txt").write_text(
        best.get(ga.COMPONENT, ""), encoding="utf-8"
    )
    report = {
        "phase": "apply-run (iteration 2 — size penalty)",
        "scope": "배관 검증만 (Addendum A)",
        "section": section,
        "target_model": adapter._target_label,
        "reflection_model": ga.provider_label(args.reflection_llm),
        "size_penalty": {"K": ga.SIZE_PENALTY_K, "deadband": ga.SIZE_PENALTY_DEADBAND},
        "before_objective": before_obj,
        "after_objective": after_obj,
        "delta_objective": round(after_obj - before_obj, 4),
        "before_quality": before_q,
        "after_quality": after_q,
        "delta_quality": round(after_q - before_q, 4),
        "seed_len": seed_len,
        "candidate_len": cand_len,
        "size_ratio": ratio,
        "size_gate_1_pass": size_gate_pass,
        "per_example_after": [
            {"id": o["id"], "objective": o["objective"], "quality": o["quality"]}
            for o in after_eval.outputs
        ],
    }
    (out_dir / f"report_{section}_{tag}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[apply] 산출물 저장: {out_dir}")
    return report


def _latest_candidate(out_dir: str, section: str) -> Path:
    cands = sorted(Path(out_dir).glob(f"candidate_{section}_*.txt"))
    if not cands:
        raise SystemExit(f"candidate 파일 없음({out_dir}) — 먼저 --apply-run 실행.")
    return cands[-1]


def _readable(raw: str) -> str:
    """모델 원응답(JSON)을 사람이 읽기 좋은 필드 나열로. 파싱 실패 시 원문."""
    try:
        d = _extract_json(raw)
        parts = [f"[{f}] {d[f]}" for f in ("summary", "advice", "conclusion", "characteristics")
                 if str(d.get(f, "")).strip()]
        return "\n".join(parts) if parts else raw.strip()
    except Exception:
        return raw.strip()


def run_make_blind(args) -> dict:
    """[Addendum B] before/after 20쌍을 익명 A/B로 생성 → 사람 블라인드 비교용.

    _answer_key는 채점 전까지 보지 말 것. A/B는 인덱스 교대로 균형 배정.
    """
    golden = _load_golden(Path(args.evalset))
    section = args.section or golden.get("default_section", "careerWealth")
    val = golden["val"]
    if args.limit:
        val = val[: args.limit]
    cand_path = Path(args.candidate) if args.candidate else _latest_candidate(args.out, section)
    cand_text = cand_path.read_text(encoding="utf-8")

    adapter = ga.SajuSectionAdapter(section, concurrency=args.concurrency, target_provider=args.target_llm)
    batch = [ga.build_datainst(it, section) for it in val]
    seed = {ga.COMPONENT: ga.seed_system_prompt(section)}
    cand = {ga.COMPONENT: cand_text}
    print(f"[blind] section={section} n={len(batch)} candidate={cand_path.name} — before/after 생성 중…")
    before = adapter.evaluate(batch, seed, capture_traces=False)
    after = adapter.evaluate(batch, cand, capture_traces=False)

    pairs = []
    for i, (b_out, a_out, data) in enumerate(zip(before.outputs, after.outputs, batch)):
        bt, at = b_out["response"], a_out["response"]
        if i % 2 == 0:  # 인덱스 교대로 A/B 균형
            opt_a, opt_b, key = bt, at, {"A": "before", "B": "after"}
        else:
            opt_a, opt_b, key = at, bt, {"A": "after", "B": "before"}
        pairs.append({
            "id": data["id"],
            "context_str": data["context_str"],
            "option_A": _readable(opt_a),
            "option_B": _readable(opt_b),
            "before_text": bt,
            "after_text": at,
            "_answer_key": key,
            "human_pref": "",   # 채울 값: "A" | "B" | "tie"
            "human_note": "",
        })

    out = {
        "_meta": {
            "purpose": "사람 블라인드 비교(Addendum B) — A/B 중 어느 쪽이 더 잘 읽히고 개인화됐는지. "
                       "채점 전까지 _answer_key 보지 말 것.",
            "section": section,
            "candidate": cand_path.name,
            "n": len(pairs),
            "how_to": "각 항목 human_pref에 'A'/'B'/'tie', human_note에 이유 기입. "
                      "다 채우면 _answer_key로 un-blind 집계. 머지 조건: after 선호 ≥ before AND 품질 회귀 없음.",
        },
        "pairs": pairs,
    }
    out_path = Path("scripts/gepa_data/human_eval_pairs.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    a_is_after = sum(1 for p in pairs if p["_answer_key"]["A"] == "after")
    print(f"[blind] {len(pairs)}쌍 작성: {out_path}  (A=after {a_is_after}/{len(pairs)} 균형)")
    print("[blind] → 사람이 human_pref 채운 뒤 un-blind 집계 필요(게이트4).")
    return out


def run_noise_check(args) -> dict:
    """[Addendum G] 셔플 재표본 재실행으로 Δquality가 노이즈인지 검증.

    LLM 샘플링 + 픽스처 구성 노이즈를 함께 본다: repeat마다 전체 70풀에서 N건을
    다르게 샘플링해 seed vs candidate를 재평가, Δquality가 모든 repeat에서 양수면 ROBUST.
    """
    golden = _load_golden(Path(args.evalset))
    section = args.section or golden.get("default_section", "careerWealth")
    pool = golden["train"] + golden["val"]
    cand_path = Path(args.candidate) if args.candidate else _latest_candidate(args.out, section)
    cand = {ga.COMPONENT: cand_path.read_text(encoding="utf-8")}
    seed = {ga.COMPONENT: ga.seed_system_prompt(section)}
    adapter = ga.SajuSectionAdapter(section, concurrency=args.concurrency, target_provider=args.target_llm)

    n = args.noise_n
    rows = []
    error: str | None = None
    # 각 repeat를 보호 — LLM/API 오류(예: 크레딧 소진) 시 완료분은 보존하고 중단.
    for r in range(args.repeats):
        try:
            sample = random.Random(1000 + r).sample(pool, min(n, len(pool)))
            batch = [ga.build_datainst(it, section) for it in sample]
            b = adapter.evaluate(batch, seed, capture_traces=False)
            a = adapter.evaluate(batch, cand, capture_traces=False)
            bq = _mean([o["quality"] for o in b.outputs])
            aq = _mean([o["quality"] for o in a.outputs])
            rows.append({"repeat": r, "n": len(batch), "before_quality": bq,
                         "after_quality": aq, "delta_quality": round(aq - bq, 4)})
            print(f"[noise] repeat {r}: before={bq} after={aq} Δquality={aq - bq:+.4f}")
        except Exception as e:  # noqa: BLE001 — 부분 결과 보존이 목적
            error = f"{type(e).__name__}: {e}"
            print(f"[noise] repeat {r} 중단 — {error}")
            break

    deltas = [x["delta_quality"] for x in rows]
    if not rows:
        verdict = "FAILED (완료된 repeat 없음)"
    elif error:
        verdict = f"INCOMPLETE ({len(rows)}/{args.repeats} repeat 완료 — 미완)"
    elif all(d > 0 for d in deltas):
        verdict = "ROBUST (모든 repeat Δ>0)"
    else:
        verdict = "NOISY (일부 repeat Δ≤0)"

    if deltas:
        mean_d = round(sum(deltas) / len(deltas), 4)
        print(f"[noise] Δquality range [{min(deltas):+.4f}, {max(deltas):+.4f}] "
              f"mean {mean_d:+.4f} → {verdict}")
    else:
        mean_d = None
        print(f"[noise] {verdict}")

    report = {
        "phase": "noise-check (Addendum G)",
        "section": section,
        "target_model": adapter._target_label,
        "candidate": cand_path.name,
        "repeats_requested": args.repeats,
        "repeats_completed": len(rows),
        "repeats": rows,
        "delta_min": min(deltas) if deltas else None,
        "delta_max": max(deltas) if deltas else None,
        "delta_mean": mean_d,
        "error": error,
        "verdict": verdict,
    }
    out_path = Path(args.out) / f"noise_{section}_{_now_tag()}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[noise] 저장: {out_path}")
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="GEPA 사주 섹션 프롬프트 최적화 (오프라인 PoC)")
    p.add_argument("--section", default="", help="섹션 키(기본: golden default_section)")
    p.add_argument("--evalset", default="scripts/gepa_data/saju_golden.json")
    p.add_argument("--out", default="scripts/gepa_results")
    p.add_argument("--max-metric-calls", type=int, default=60)
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--limit", type=int, default=0, help="baseline/blind에서 val 일부만(비용 절감)")
    p.add_argument("--samples", type=int, default=5, help="샘플 출력 건수")
    p.add_argument("--candidate", default="", help="candidate 프롬프트 파일(기본: out의 최신)")
    p.add_argument("--repeats", type=int, default=3, help="noise-check 반복 횟수")
    p.add_argument("--noise-n", type=int, default=20, help="noise-check 표본 크기")
    p.add_argument("--target-llm", default="9b", choices=["9b", "14b", "main", "commercial"],
                   help="target/rollout 모델 (기본 9b=로컬 MLX 비용0; main/commercial=Anthropic 유료)")
    p.add_argument("--reflection-llm", default="14b", choices=["9b", "14b", "main", "commercial"],
                   help="reflection 모델 (기본 14b=로컬 MLX 비용0)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="기본: baseline만, GEPA 미실행")
    mode.add_argument("--apply-run", action="store_true", help="실제 GEPA 진화(사람 승인 후)")
    mode.add_argument("--make-blind", action="store_true", help="before/after 20쌍 익명 생성(Addendum B)")
    mode.add_argument("--noise-check", action="store_true", help="셔플 재실행 노이즈 검증(Addendum G)")
    args = p.parse_args()

    if args.apply_run:
        run_apply(args)
    elif args.make_blind:
        run_make_blind(args)
    elif args.noise_check:
        run_noise_check(args)
    else:
        run_baseline(args)


if __name__ == "__main__":
    main()
