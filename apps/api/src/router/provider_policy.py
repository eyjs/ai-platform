"""Provider Router Policy Engine (순수함수).

Profile YAML `providers:` 블록을 파싱하여 우선순위/조건 기반의
ProviderPolicy 를 생성하고, 현재 활성 Provider Pool 과 교집합으로
primary / fallback chain 을 계산한다.

I/O 없음. 상태 없음. 테스트 용이.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.infrastructure.providers.base import ProviderCapability


@dataclass(frozen=True)
class ProviderCandidate:
    provider_id: str
    priority: int = 100
    require_tool_use: bool = False
    require_streaming: bool = False
    max_cost_per_1k: Optional[float] = None


@dataclass(frozen=True)
class ProviderPolicy:
    candidates: tuple[ProviderCandidate, ...]
    fallback_on: tuple[str, ...] = ("timeout", "5xx", "unavailable")
    max_fallback_depth: int = 2


_DEFAULT_POLICY = ProviderPolicy(
    candidates=(ProviderCandidate(provider_id="__default__", priority=1),),
    fallback_on=("timeout", "5xx", "unavailable"),
    max_fallback_depth=2,
)


def parse_policy(profile_config: dict | None) -> ProviderPolicy:
    """Profile YAML 의 providers 블록을 ProviderPolicy 로 변환.

    블록이 없거나 비어있으면 기본 정책 반환 (placeholder: "__default__").
    호출자는 "__default__" 를 실제 main provider id 로 대체한다.
    """
    if not profile_config:
        return _DEFAULT_POLICY
    providers_block = profile_config.get("providers")
    if not providers_block or not providers_block.get("candidates"):
        return _DEFAULT_POLICY

    raw_candidates = providers_block["candidates"]
    parsed: list[ProviderCandidate] = []
    for raw in raw_candidates:
        pid = raw.get("provider_id")
        if not pid:
            continue
        parsed.append(
            ProviderCandidate(
                provider_id=str(pid),
                priority=int(raw.get("priority", 100)),
                require_tool_use=bool(raw.get("require_tool_use", False)),
                require_streaming=bool(raw.get("require_streaming", False)),
                max_cost_per_1k=(float(raw["max_cost_per_1k"]) if "max_cost_per_1k" in raw else None),
            )
        )
    if not parsed:
        return _DEFAULT_POLICY

    # priority 오름차순 정렬 (숫자 작을수록 먼저)
    parsed.sort(key=lambda c: c.priority)

    fb_on = providers_block.get("fallback_on", ["timeout", "5xx", "unavailable"])
    depth = int(providers_block.get("max_fallback_depth", 2))
    depth = max(0, min(depth, 2))

    return ProviderPolicy(
        candidates=tuple(parsed),
        fallback_on=tuple(fb_on),
        max_fallback_depth=depth,
    )


def _candidate_matches(candidate: ProviderCandidate, cap: ProviderCapability) -> bool:
    """candidate 조건이 capability 에 부합하는지."""
    if candidate.provider_id != cap.provider_id and candidate.provider_id != "__default__":
        return False
    if candidate.require_tool_use and not cap.supports_tool_use:
        return False
    if candidate.require_streaming and not cap.supports_streaming:
        return False
    if candidate.max_cost_per_1k is not None and cap.cost_per_1k_tokens > candidate.max_cost_per_1k:
        return False
    return True


def select_primary(
    policy: ProviderPolicy,
    available: list[ProviderCapability],
) -> Optional[ProviderCapability]:
    """첫 매칭 (priority 오름차순) primary 선택. 없으면 None."""
    if not available:
        return None
    for cand in policy.candidates:
        for cap in available:
            if _candidate_matches(cand, cap):
                return cap
    return None


def select_fallback_chain(
    policy: ProviderPolicy,
    available: list[ProviderCapability],
    exclude_ids: tuple[str, ...],
) -> list[ProviderCapability]:
    """exclude_ids 를 제외한 fallback 후보. 최대 max_fallback_depth 개."""
    chain: list[ProviderCapability] = []
    seen = set(exclude_ids)
    for cand in policy.candidates:
        for cap in available:
            if cap.provider_id in seen:
                continue
            if _candidate_matches(cand, cap):
                chain.append(cap)
                seen.add(cap.provider_id)
                if len(chain) >= policy.max_fallback_depth:
                    return chain
    return chain
