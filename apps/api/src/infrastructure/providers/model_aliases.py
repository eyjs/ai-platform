"""모델 별칭(alias) 은퇴 가드 — infrastructure layer (C7).

resolve_model_alias(alias, settings) → 모델 ID 또는 ""

## 이 모듈이 아직 존재하는 이유

원래는 백엔드별 alias 매핑 테이블이었다. "haiku"/"sonnet"/"opus" 를 provider_mode 에 따라
Claude ID·GPT ID·로컬 모델명으로 풀어줬다. 상용 퇴역(2026-07-16)으로 풀어줄 대상이 사라졌고,
남은 값(DGX 태그)은 이미 구체 ID라 풀어줄 게 없다. 매핑 테이블은 통째로 지웠다.

그런데 모듈을 아예 지울 수는 없다. **DB·시드에 남아 있는 "haiku" 가 실재하기 때문이다.**
router_model 제거(a340905) 당시 라이브 6개 프로필이 전부 router_model="haiku" 를 들고
있었고 main_model 기본값도 "haiku" 였다. 프로필은 DB 에 있고 운영자가 편집한다 —
코드에서 상수를 지운다고 데이터가 따라 사라지지 않는다.

그래서 이 모듈은 "매핑"이 아니라 **은퇴한 이름을 걸러내는 가드**로 남는다:

  은퇴 alias("haiku"/"sonnet"/"opus") → "" + WARN (호출부가 기본 모델로 폴백)
  그 외(구체 모델 ID)                → 그대로 통과

## "" 를 돌려주는 게 왜 중요한가 (지우지 말 것)

ProviderFactory._split_model_request 는 DGX /api/tags 카탈로그에 있는 이름만 주 경로에
태운다. 만약 여기서 "haiku" 를 그대로 통과시키면 카탈로그에 없으니 폴백으로 밀려나고,
폴백이 답을 주니 **겉보기엔 멀쩡한 채 DGX 가 통째로 우회된다**(실측 사고). "" 는 그
경로를 원천 차단한다 — 호출부(agentic_executor)가 아예 오버라이드를 만들지 않고
부트스트랩 기본 모델(=DGX)을 쓰기 때문이다. 즉 카탈로그 신뢰(_split_model_request)와
이 가드는 같은 사고를 두 겹으로 막는다. 한 겹만 남기지 말 것.

조용히 지나가지 않고 WARN 을 남기는 이유: 프로필이 은퇴한 이름을 들고 있다는 건 데이터
정리가 안 끝났다는 뜻이고, 그건 사람이 고쳐야 한다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger(__name__)

# 상용 티어 이름. 2026-07-16 상용 퇴역으로 가리킬 모델이 없어졌다.
_RETIRED_ALIASES: frozenset[str] = frozenset({"haiku", "sonnet", "opus"})


def resolve_model_alias(alias: str, settings: "Settings | None" = None) -> str:
    """은퇴한 상용 alias를 걸러내고, 구체 모델 ID는 그대로 통과시킨다.

    Args:
        alias: profile.main_model 에 담긴 값. DGX 모델 태그("qwen3.6:35b-a3b")이거나,
               아직 정리 안 된 은퇴 alias("haiku")일 수 있다.
        settings: 시그니처 호환용 — 현재 미사용(해석에 설정이 필요 없다). 상용 시절엔
                  provider_mode 로 매핑 테이블을 골랐다.

    Returns:
        구체 모델 ID, 또는 "" (은퇴 alias/빈 값). 호출부는 "" 이면 기본(부트스트랩) 모델을
        써야 하고, 그게 곧 DGX 기본 모델이다.
    """
    alias = (alias or "").strip()
    if not alias:
        return ""

    if alias.lower() in _RETIRED_ALIASES:
        logger.warning(
            "retired_model_alias_ignored",
            extra={
                "alias": alias,
                "hint": (
                    "상용 티어 이름은 2026-07-16 퇴역했다 — 기본 DGX 모델로 폴백한다. "
                    "프로필의 main_model 을 DGX 태그(예: qwen3.6:35b-a3b)로 바꾸거나 비울 것."
                ),
            },
        )
        return ""

    return alias
