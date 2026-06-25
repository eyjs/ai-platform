"""모델 별칭(alias) 해석기 — infrastructure layer (C7).

resolve_model_alias(alias, settings) → 구체 모델 ID 또는 ""

설계 원칙:
- Settings만 읽는 순수 함수. Provider/Router/Factory 임포트 금지.
- 알 수 없거나 확신 없는 alias → "" 반환(호출부가 기본 모델로 폴백).
- 절대 예외 발생 없음. 불확실하면 ""를 반환하고 debug 로그를 남긴다.
- 레이어 단방향: infrastructure(C7)는 Settings만 의존. Agent/Router가 여기를 import 가능.

백엔드별 alias 매핑 테이블 (provider_mode 기준):

  anthropic:
    "haiku"  → settings.anthropic_main_model 이 haiku 패밀리면 그대로, 아니면 "claude-haiku-4-5"
    "sonnet" → "claude-sonnet-4-5"
    "opus"   → "claude-opus-4-5"
    <구체 ID> (claude-로 시작) → 그대로 통과

  development-ollama (provider_mode=development, server_url 없음):
    "haiku"  → settings.router_model (빠른 경량 모델)
    "sonnet" → settings.main_model   (주력 모델)
    "opus"   → settings.main_model   (로컬에는 opus 개념 없음 → main 으로 최선 근사)
    <구체 비 alias> → 그대로 통과

  development-http (provider_mode=development, main_llm_server_url 있음):
    "haiku"  → settings.router_model (빠른 라우터 모델)
    "sonnet" → settings.main_model
    "opus"   → settings.main_model
    <구체 비 alias> → 그대로 통과

  openai/production:
    "haiku"  → "gpt-4o-mini"
    "sonnet" → settings.prod_llm_model  ("gpt-4o-mini" 또는 "gpt-4o")
    "opus"   → "gpt-4o"
    <구체 ID> (gpt-로 시작) → 그대로 통과

KNOWN GAP (P0-3):
  router_model 은 ExecutionPlan.router_model 까지 흘러오고 resolve_model_alias 로
  구체 ID로 해석할 수 있다. 그러나 현재 L0/L1/L2 라우팅 LLM (AIRouter,
  ChainResolver, IntentClassifier, SemanticClassifier)은 부트스트랩 시점에
  바인딩된 router_llm 인스턴스를 사용하며, 요청 시점의 plan.router_model을
  반영하지 않는다.
  → 다음 이터레이션 seam: AIRouter.route 가 profile.router_model 을 인자로 받고
    per-request 오버라이드 LLM을 서브컴포넌트에 주입하도록 리팩터링.
  현재는 router_model 이 plan 에 실려 있을 뿐, 실제 라우팅 LLM 교체는 미구현.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger(__name__)

# anthropic 모드: alias → 구체 Claude 모델 ID
_ANTHROPIC_ALIASES: dict[str, str] = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-5",
    "opus": "claude-opus-4-5",
}

# openai/production 모드: alias → 구체 GPT 모델 ID
_OPENAI_ALIASES: dict[str, str] = {
    "haiku": "gpt-4o-mini",
    "sonnet": "gpt-4o-mini",
    "opus": "gpt-4o",
}

# 알려진 단순 alias 집합 (이 외의 값은 "구체 ID"로 간주해 통과)
_KNOWN_ALIASES: frozenset[str] = frozenset({"haiku", "sonnet", "opus"})


def resolve_model_alias(alias: str, settings: "Settings | None") -> str:
    """alias 문자열을 provider_mode 에 맞는 구체 모델 ID로 변환한다.

    Args:
        alias: profile.main_model / profile.router_model 에 담긴 값.
               "haiku", "sonnet", "opus" 같은 논리 별칭이거나
               "claude-haiku-4-5" 같은 구체 ID일 수 있다.
        settings: Settings 인스턴스. None 이면 "" 반환(폴백).

    Returns:
        구체 모델 ID 문자열. 해석 불가이거나 비어있으면 "" 반환.
        호출부는 "" 이면 기본(bootstrap) 모델을 사용해야 한다.
    """
    if not alias or not settings:
        return ""

    alias = alias.strip()
    if not alias:
        return ""

    try:
        from src.config import ProviderMode
        mode = settings.provider_mode
    except Exception as exc:
        logger.debug("resolve_model_alias: settings read error (%s), returning ''", exc)
        return ""

    # anthropic 모드
    if mode == ProviderMode.ANTHROPIC:
        # 구체 Claude ID 직접 통과
        if alias.startswith("claude-"):
            return alias
        resolved = _ANTHROPIC_ALIASES.get(alias, "")
        if not resolved:
            logger.debug(
                "resolve_model_alias: unknown alias '%s' for anthropic backend, returning ''",
                alias,
            )
        return resolved

    # openai / production 모드
    if mode in (ProviderMode.OPENAI, ProviderMode.PRODUCTION):
        # 구체 GPT ID 직접 통과
        if alias.startswith("gpt-") or alias.startswith("o1") or alias.startswith("o3"):
            return alias
        resolved = _OPENAI_ALIASES.get(alias, "")
        if not resolved:
            logger.debug(
                "resolve_model_alias: unknown alias '%s' for openai backend, returning ''",
                alias,
            )
        return resolved

    # development 모드 (ollama 또는 http)
    if mode == ProviderMode.DEVELOPMENT:
        # 논리 alias 매핑: haiku → router_model (경량), sonnet/opus → main_model
        if alias == "haiku":
            return settings.router_model or ""
        if alias in ("sonnet", "opus"):
            return settings.main_model or ""
        # alias 집합에 없는 값 = 구체 ID로 취급해 그대로 통과
        if alias not in _KNOWN_ALIASES:
            return alias
        # _KNOWN_ALIASES 중 위에서 처리 안 된 경우(이론상 없음)
        logger.debug(
            "resolve_model_alias: unhandled alias '%s' for development backend, returning ''",
            alias,
        )
        return ""

    # 미지원 모드
    logger.debug(
        "resolve_model_alias: unsupported provider_mode '%s', returning ''",
        mode,
    )
    return ""
