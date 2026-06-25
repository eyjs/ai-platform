"""GEPA PoC — 사주 섹션 GEPAAdapter + 모델 배선.

[Addendum E] target/rollout 모델 = ProviderFactory().get_main_llm() — 운영 사주
리포트가 실제로 이 경로로 서빙된다(bootstrap.py:215, saju_report_service.py:42).
reflection 모델 = get_commercial_llm()(Anthropic Haiku).

이 어댑터는 운영 런타임 코드를 호출만 할 뿐 수정하지 않는다(읽기 전용 import).
- seed prompt: get_paper_section_prompt(section) → (system, user_template)
- target 호출: provider.generate(prompt=user, system=candidate_system) — 운영과 동일 형태

GEPAAdapter 인터페이스(gepa 0.1.1):
  evaluate(batch, candidate, capture_traces) -> EvaluationBatch(outputs, scores, trajectories, objective_scores)
  make_reflective_dataset(candidate, eval_batch, components_to_update) -> Mapping[str, Sequence[Mapping]]
"""

from __future__ import annotations

import asyncio
from typing import Any

from gepa.core.adapter import EvaluationBatch, GEPAAdapter

from src.config import settings
from src.infrastructure.providers.factory import ProviderFactory
from src.tools.internal.saju_context_formatter import format_single_person_context
from src.tools.internal.saju_prompts import get_paper_section_prompt

from gepa_metrics import score_saju_section


def _ensure_locale() -> None:
    """LocaleBundle 싱글턴 초기화(읽기 전용 재사용) — 평소 bootstrap이 하던 일.

    factory가 get_locale().prompt(...)를 호출하므로 오프라인 스크립트도 1회 로드해야 한다.
    런타임 코드는 수정하지 않고 같은 로더(LocaleBundle.load)를 호출만 한다.
    """
    from pathlib import Path

    from src.locale.bundle import LocaleBundle, get_locale, set_locale

    try:
        get_locale()
        return  # 이미 초기화됨
    except RuntimeError:
        pass
    # apps/api/src/locale/{locale}.yaml — 이 파일(scripts/)에서 두 단계 위가 apps/api.
    locale_path = Path(__file__).resolve().parent.parent / "src" / "locale" / f"{settings.locale}.yaml"
    set_locale(LocaleBundle.load(str(locale_path)))

# GEPA가 진화시킬 단일 컴포넌트(섹션 system prompt) 키.
COMPONENT = "saju_section_system"

# 단일 영속 이벤트 루프 — gepa.optimize는 sync로 evaluate()/reflection을 수백 번 호출한다.
# asyncio.run()을 매번 쓰면 루프가 매번 생성·종료되어 AsyncAnthropic 클라이언트·Semaphore가
# 죽은 루프에 바인딩된다("bound to a different event loop"). 한 루프를 재사용해 해결.
_LOOP: "asyncio.AbstractEventLoop | None" = None


def _run_sync(coro):
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP.run_until_complete(coro)


# ── 프롬프트 크기 패널티 (사용자 요청: 게이트1 폭주 방지) ──────────────
# 1차 apply-run에서 GEPA가 프롬프트를 seed의 1.85x(게이트 한도 1.5x 초과)로 폭주시킴.
# metric은 출력만 채점해 프롬프트 길이를 모르므로, candidate 길이 패널티를 objective에
# 직접 넣어 GEPA가 '간결함의 비용'을 느끼게 한다. seed 대비 비율 deadband 이후 선형.
#   penalty = K * max(0, ratio - DEADBAND),  ratio = len(candidate)/len(seed)
# 예) K=0.3, DEADBAND=1.15 → 1.5x:0.105, 1.85x:0.21 (둘 다 관측 품질이득 ~0.025보다 큼
#     → 게이트 넘는 게 손해 → GEPA가 압축하도록 유도)
SIZE_PENALTY_K = 0.3
SIZE_PENALTY_DEADBAND = 1.15

# ── LLM provider 선택 (비용 0 로컬 MLX 기본) ──────────────────────────
# 8106=Qwen3.5-9B(브리프 명시 target), 8104=Qwen3-14B(더 강함, reflection 권장).
# main/commercial은 Anthropic Haiku(유료) — 명시적으로 골라야만 사용.
LOCAL_LLM_URLS = {"9b": "http://localhost:8106", "14b": "http://localhost:8104"}


def provider_label(which: str) -> str:
    return {
        "9b": "local_mlx:Qwen3.5-9B(8106)",
        "14b": "local_mlx:Qwen3-14B(8104)",
        "local": "local_mlx:Qwen3.5-9B(8106)",
        "main": "anthropic:get_main_llm(유료)",
        "commercial": "anthropic:Haiku(유료)",
    }.get(which, which)


def resolve_provider(factory, which: str, max_tokens: int = 1024):
    """provider 키 → LLMProvider. local(9b/14b)은 MLX HTTP(비용0), main/commercial은 Anthropic."""
    if which == "local":
        which = "9b"
    if which in LOCAL_LLM_URLS:
        from src.locale.bundle import get_locale
        from src.infrastructure.providers.llm.http_llm import HttpLLMProvider

        prefix = get_locale().prompt("llm_system_prefix")
        return HttpLLMProvider(base_url=LOCAL_LLM_URLS[which], system_prefix=prefix, max_tokens=max_tokens)
    if which == "commercial":
        return factory.get_commercial_llm()
    if which == "main":
        return factory.get_main_llm()
    raise ValueError(f"unknown provider: {which!r} (택: 9b, 14b, main, commercial)")

# 오행 영문→한글(용신 토큰 정규화용)
_ELEMENT_KO = {"wood": "목", "fire": "화", "earth": "토", "metal": "금", "water": "수"}
# 천간 → 한자(일간 변형 표기 허용용)
_STEM_HANJA = {
    "갑": "甲", "을": "乙", "병": "丙", "정": "丁", "무": "戊",
    "기": "己", "경": "庚", "신": "辛", "임": "壬", "계": "癸",
}
# 천간 → 오행 한글(갑→목 등): "갑목" 같은 표기 변형 허용용
_STEM_ELEMENT_KO = {
    "갑": "목", "을": "목", "병": "화", "정": "화", "무": "토",
    "기": "토", "경": "금", "신": "금", "임": "수", "계": "수",
}


def expected_tokens(saju_data: dict) -> dict:
    """이 사람 사주에서 개인화 채점 스펙을 추출한다.

    [사용자 결정: 변형 허용]
      core (분모): 일간 천간, 우세 오행, 용신 오행, 신살 — 반드시 짚어야 개인화.
      bonus (가점): 일주 compound(갑인), 신강약 status — 있으면 +, 없어도 감점 안 함.
      variants: 일간 표기 변형(갑/甲/갑목) 매칭 허용 맵.

    Returns: {"core": [...], "bonus": [...], "variants": {token: [변형...]}, "display": [...]}
    """
    core: list[str] = []
    bonus: list[str] = []
    variants: dict[str, list[str]] = {}

    basic = saju_data.get("basic", {})
    day = basic.get("fourPillars", {}).get("day", {})
    stem = day.get("heavenlyStem")
    branch = day.get("earthlyBranch")
    if stem:
        core.append(stem)  # 일간 천간 (core)
        vs = []
        if stem in _STEM_HANJA:
            vs.append(_STEM_HANJA[stem])
        if stem in _STEM_ELEMENT_KO:
            vs.append(f"{stem}{_STEM_ELEMENT_KO[stem]}")  # 예: 갑목
        if vs:
            variants[stem] = vs
    if stem and branch:
        bonus.append(f"{stem}{branch}")  # 일주 compound (bonus)

    interp = saju_data.get("premium", {}).get("interpretation", {})
    energy = interp.get("energyScore", {})
    elements = {k: energy.get(k) for k in _ELEMENT_KO if isinstance(energy.get(k), (int, float))}
    if elements:
        core.append(_ELEMENT_KO[max(elements, key=elements.get)])  # 우세 오행 (core)

    yongsin = interp.get("yongsin", {}).get("yongsin")
    if yongsin in _ELEMENT_KO:
        core.append(_ELEMENT_KO[yongsin])  # 용신 오행 (core)
    elif yongsin:
        core.append(str(yongsin))

    for s in interp.get("shinsal", []) or []:
        if isinstance(s, str) and s:
            core.append(s)  # 신살 (core)

    if energy.get("selfStatus"):
        bonus.append(str(energy["selfStatus"]))  # 신강약 (bonus)

    # 중복 제거(순서 보존)
    def _dedup(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        return [x for x in xs if not (x in seen or seen.add(x))]

    core, bonus = _dedup(core), _dedup(bonus)
    return {"core": core, "bonus": bonus, "variants": variants, "display": core + bonus}


def build_datainst(item: dict, section_key: str) -> dict:
    """golden 픽스처 항목 → GEPA DataInst(dict)."""
    saju_data = item["saju_data"]
    pers = expected_tokens(saju_data)
    return {
        "id": item.get("id", ""),
        "section_key": section_key,
        "context_str": format_single_person_context(saju_data, "사용자"),
        "pers": pers,
        "tokens": pers["display"],  # 표시용(core+bonus)
    }


def seed_system_prompt(section_key: str) -> str:
    """해당 섹션의 운영 seed system prompt(읽기 전용)."""
    system, _user = get_paper_section_prompt(section_key)
    return system


def _user_template(section_key: str) -> str:
    _system, user = get_paper_section_prompt(section_key)
    return user


class SajuSectionAdapter(GEPAAdapter):
    """사주 단일 섹션 프롬프트 최적화용 어댑터.

    candidate = {COMPONENT: <section system prompt text>} 를 받아,
    각 DataInst(사주 입력)에 대해 target LLM을 돌리고 결정론적 metric으로 채점한다.
    """

    def __init__(self, section_key: str, concurrency: int = 5,
                 target_provider: str = "local") -> None:
        self._section = section_key
        self._user_tmpl = _user_template(section_key)
        self._concurrency = concurrency
        self._seed_len = len(seed_system_prompt(section_key))  # 크기 패널티 기준
        _ensure_locale()
        self._factory = ProviderFactory(settings)
        self._target_provider = target_provider
        # target 선택: local(MLX Qwen, 비용0·기본) | main/commercial(Anthropic Haiku, 유료)
        # max_tokens=2048 — 사주 리포트 JSON(summary+advice+conclusion+characteristics ≈830자)
        # 이 1024로는 잘려 JSON 파싱 실패. 로컬 9B에 충분한 여유 부여.
        self._target = resolve_provider(self._factory, target_provider, max_tokens=2048)
        self._target_label = provider_label(target_provider)

    def _system_of(self, candidate: dict[str, str]) -> str:
        return candidate.get(COMPONENT) or next(iter(candidate.values()))

    def _size_penalty(self, system: str) -> tuple[float, float]:
        """candidate 프롬프트 길이 패널티. Returns (penalty, ratio).

        ratio = len/seed_len. deadband 이후 선형 — GEPA가 '간결함의 비용'을 느끼게 함.
        """
        ratio = len(system) / max(1, self._seed_len)
        penalty = SIZE_PENALTY_K * max(0.0, ratio - SIZE_PENALTY_DEADBAND)
        return penalty, ratio

    async def _agenerate(self, system: str, context_str: str, sem: asyncio.Semaphore) -> str:
        user = self._user_tmpl.replace("{context_str}", context_str)
        async with sem:
            # 운영과 동일 형태: generate(prompt=user, system=section_system)
            # 로컬 단일 GPU MLX 서버는 동시 요청 시 connection drop(ReadError) 가능 →
            # 일시 오류는 백오프 재시도(최대 3회). 영속 오류면 마지막 예외 전파.
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    return await self._target.generate(prompt=user, system=system)
                except Exception as e:  # noqa: BLE001 — 일시 전송오류 재시도가 목적
                    last_err = e
                    await asyncio.sleep(0.8 * (attempt + 1))
            raise last_err  # type: ignore[misc]

    async def _arun_batch(self, system: str, batch: list[dict]) -> list[str]:
        # Semaphore는 코루틴 내부(현재 루프)에서 생성 → 루프 바인딩 안전.
        sem = asyncio.Semaphore(self._concurrency)
        return await asyncio.gather(
            *(self._agenerate(system, d["context_str"], sem) for d in batch)
        )

    def evaluate(
        self,
        batch: list[dict],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch:
        system = self._system_of(candidate)
        # evaluate는 sync 계약 — 영속 루프에서 async 배치 실행(루프 재사용으로 클라이언트 안정).
        responses = _run_sync(self._arun_batch(system, batch))

        # 크기 패널티는 candidate 속성 → 배치 전체에 동일 적용.
        penalty, ratio = self._size_penalty(system)
        size_note = ""
        if penalty > 0:
            size_note = (
                f" | [길이패널티 -{penalty:.3f}] system prompt가 seed의 {ratio:.2f}x로 너무 김 → "
                "규칙·지침을 더 간결하게 압축하라(품질 유지하며 글자 수 줄이기)."
            )

        outputs: list[dict[str, Any]] = []
        scores: list[float] = []
        trajectories: list[dict[str, Any]] | None = [] if capture_traces else None

        for data, raw in zip(batch, responses, strict=True):
            result = score_saju_section(self._section, raw, data["pers"])
            quality = result.score
            objective = max(0.0, quality - penalty)  # GEPA가 최적화하는 값
            feedback = result.feedback + size_note
            scores.append(objective)
            outputs.append({
                "id": data["id"],
                "response": raw,
                "feedback": feedback,
                "breakdown": result.breakdown,
                "quality": quality,        # 원 metric(출력 품질)
                "size_penalty": penalty,   # candidate 길이 패널티
                "objective": objective,    # quality - penalty
            })
            if trajectories is not None:
                trajectories.append({
                    "context_str": data["context_str"],
                    "tokens": data["tokens"],
                    "response": raw,
                    "feedback": feedback,
                    "score": objective,
                    "quality": quality,
                })

        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=trajectories)

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch,
        components_to_update: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        traj = eval_batch.trajectories or []
        records: list[dict[str, Any]] = []
        for t in traj:
            records.append({
                "Inputs": (
                    f"[섹션 {self._section}] 이 사람 사주 데이터(요약):\n{t['context_str']}\n"
                    f"반드시 짚어야 할 개인화 토큰: {t['tokens']}"
                ),
                "Generated Output": t["response"],
                "Feedback": t["feedback"],
            })
        return {comp: records for comp in components_to_update}


def make_reflection_lm(provider: str = "14b"):
    """GEPA reflection_lm 콜러블 — 기본 로컬 14B(비용0). provider로 모델 선택.

    LanguageModel Protocol: __call__(prompt: str | list[dict]) -> str
    reflection은 새 프롬프트를 생성하므로 토큰 여유(2048) 확보.
    """
    _ensure_locale()
    factory = ProviderFactory(settings)
    reflector = resolve_provider(factory, provider, max_tokens=2048)

    def _call(prompt: str | list[dict]) -> str:
        if isinstance(prompt, list):
            text = "\n\n".join(
                str(m.get("content", "")) if isinstance(m, dict) else str(m) for m in prompt
            )
        else:
            text = str(prompt)
        return _run_sync(reflector.generate(prompt=text, system=""))

    return _call
