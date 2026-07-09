"""KMS 회사도메인 + 카테고리경로 → ai-platform 상품도메인 매핑 로더.

근본해결: KMS 는 회사중심 도메인(예: DB-DAMAGE)으로 문서를 배치하지만, ai-platform
RAG·챗봇은 상품중심 도메인(자동차보험/건강보험/…)으로 검색 스코프를 건다. 동기화 시
(KMS 도메인 + categoryPath) → 상품도메인 매핑을 적용해 챗봇이 검색할 수 있게 한다.

매핑은 설정(`seeds/domain_mapping.yaml`)으로 외부화한다 — 새 상품/카테고리 추가 시
코드 변경 없이 설정만 갱신(P1-2 운영성). KMS=분류 SoT(ADR-009), ai-platform 은 해석만.

계약: categoryPath = ["<도메인코드>", "<최상위카테고리>", ...하위]. 매핑 키는
categoryPath[1:] 를 "/" 로 결합한 경로이며, **가장 구체적(긴 경로)부터** 매칭한다.
미매핑/부재 시 None 을 반환하고, 호출측(kms_sync)이 회사도메인 fallback + WARN 한다
(조용한 누락 0).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import yaml

from src.observability.logging import get_logger

logger = get_logger(__name__)

# 도메인 단위 기본 매핑 키. categoryPath 에 카테고리가 없거나(코드만 옴) 매핑
# 미정의일 때 이 규칙으로 폴백한다 — "검색 불가능한 회사코드 태깅"보다 낫다.
DEFAULT_RULE_KEY = "_default"

# 기본 매핑 파일 위치: 패키지 루트(apps/api)의 seeds/domain_mapping.yaml.
# CWD 와 무관하게 해석하되, 환경변수(AIP_DOMAIN_MAPPING_PATH)로 오버라이드 가능.
# domain_mapping.py: apps/api/src/services/domain_mapping.py → parents[2] = apps/api.
_DEFAULT_MAPPING_PATH = Path(__file__).resolve().parents[2] / "seeds" / "domain_mapping.yaml"

# 프로세스 시작 시 1회 로드 후 캐시(불변). 스레드 안전을 위해 락으로 보호.
_lock = threading.Lock()
_cache: dict[str, dict[str, str]] | None = None


def _resolve_mapping_path() -> Path:
    override = os.environ.get("AIP_DOMAIN_MAPPING_PATH")
    return Path(override) if override else _DEFAULT_MAPPING_PATH


def _load_mapping() -> dict[str, dict[str, str]]:
    """매핑 YAML 을 로드하여 {도메인: {카테고리경로: 상품도메인}} 으로 반환한다.

    파일 부재/파싱 실패 시 빈 매핑을 반환하고 WARN 한다 — 매핑이 없으면 호출측이
    회사도메인 fallback 으로 동작하므로 동기화 자체는 막지 않는다(조용한 누락은
    fallback WARN 으로 가시화).
    """
    path = _resolve_mapping_path()
    if not path.exists():
        logger.warning("domain_mapping_file_missing", path=str(path))
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        logger.error("domain_mapping_parse_error", path=str(path), error=str(e))
        return {}

    if not isinstance(raw, dict):
        logger.error("domain_mapping_invalid_root", path=str(path), type=type(raw).__name__)
        return {}

    # 스키마 검증: {도메인(str): {경로(str): 상품도메인(str)}}. 불신 입력 방어.
    normalized: dict[str, dict[str, str]] = {}
    for domain, rules in raw.items():
        if not isinstance(domain, str) or not isinstance(rules, dict):
            logger.warning("domain_mapping_skip_entry", domain=str(domain))
            continue
        clean: dict[str, str] = {}
        for category_path, product in rules.items():
            if isinstance(category_path, str) and isinstance(product, str):
                clean[category_path] = product
            else:
                logger.warning("domain_mapping_skip_rule", domain=domain, key=str(category_path))
        normalized[domain] = clean
    return normalized


def get_mapping() -> dict[str, dict[str, str]]:
    """캐시된 매핑을 반환한다(최초 호출 시 1회 로드)."""
    global _cache
    if _cache is None:
        with _lock:
            if _cache is None:
                _cache = _load_mapping()
                logger.info("domain_mapping_loaded", domains=list(_cache.keys()))
    return _cache


def reload_mapping() -> dict[str, dict[str, str]]:
    """매핑을 강제로 다시 로드한다(운영 갱신/테스트용)."""
    global _cache
    with _lock:
        _cache = _load_mapping()
    return _cache


def resolve_product_domain(domain: str, category_path: list[str]) -> str | None:
    """(회사도메인, categoryPath) → 상품도메인. 미매핑/부재 시 None.

    매칭 규칙:
      - categoryPath[1:] (도메인코드 제외한 카테고리들)을 "/" 로 결합한 경로를,
        **가장 구체적(긴 경로)부터** 점진적으로 줄여가며 매핑 키와 대조한다.
        예) ["DB-DAMAGE","장기보험","건강"] → "장기보험/건강" 매칭, 없으면 "장기보험".
      - 도메인이 매핑에 없거나 categoryPath 가 비면(또는 도메인코드만 있으면) None.

    Args:
        domain: KMS 회사도메인 코드(예: "DB-DAMAGE"). categoryPath[0] 와 동일해야 정상.
        category_path: KMS 가 전달한 카테고리경로(도메인코드 포함). 부재 시 빈 리스트.

    Returns:
        매핑된 상품도메인(예: "자동차보험") 또는 None(미매핑/부재 → 호출측 fallback).
    """
    if not domain:
        return None
    rules = get_mapping().get(domain)
    if not rules:
        return None

    # categoryPath[0] 은 도메인코드이므로 제외하고 카테고리들만 사용.
    categories = category_path[1:] if category_path else []
    # 빈 값/공백 정리(불신 입력 방어).
    categories = [c for c in categories if isinstance(c, str) and c.strip()]
    if not categories:
        # 실사고(2026-07-08): KMS 가 categoryPath=["D02"](코드만)를 보내는 회귀 발생 —
        # 카테고리 부재 시 무조건 None 이면 해당 도메인 문서가 영영 회사코드로 태깅되어
        # 상품도메인 스코프 프로필의 검색에서 전부 빠진다. 도메인 단위 기본 매핑
        # `_default` 를 허용해 최소한 검색 가능한 도메인으로 착지시킨다(없으면 기존과 동일 None).
        return rules.get(DEFAULT_RULE_KEY)

    # 가장 구체적(긴 경로)부터 매칭: 전체 → 한 단계씩 상위로 축소.
    for depth in range(len(categories), 0, -1):
        key = "/".join(categories[:depth])
        product = rules.get(key)
        if product:
            return product
    # 카테고리는 왔지만 매핑 미정의 → 도메인 기본 매핑으로 폴백(없으면 None).
    return rules.get(DEFAULT_RULE_KEY)
