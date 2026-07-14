"""질문 기반 엔티티 메타필터 — RAG 깔때기 1단계 (P2).

코퍼스에 실제로 존재하는 문서명에서 별칭(상품명·문서유형)을 추출해 인덱스를
만들고, 질문에 별칭이 등장하면 해당 문서들로 검색 범위를 좁힌다.

원칙 (로컬 LLM 원칙 — 판단은 신호·결정성으로):
- 별칭 추출·매칭 전부 결정론적 문자열 처리. LLM 판단 없음.
- 별칭은 코퍼스에서 유도하므로 도메인 무관(보험/은행/무엇이든).
- 전체 문서의 과반에 걸리는 별칭은 변별력이 없어 버린다(예: 회사명, "무배당").
- 매칭 실패 = 필터 없음(기존 동작). 필터 검색이 빈손이면 호출부가 무필터 폴백.
"""

import re
import time
from dataclasses import dataclass, field

from src.observability.logging import get_logger

logger = get_logger(__name__)

# 별칭 최소 길이 — 너무 짧은 토큰("보험", "약관" 단독 등)의 과매칭 방지
MIN_ALIAS_LENGTH = 4
# 전체 문서 중 이 비율을 초과해 걸리는 별칭은 변별력 없음 → 제외
MAX_COVERAGE_RATIO = 0.5
# 인덱스 갱신 주기 (초) — 문서 추가/삭제 반영
INDEX_TTL_SECONDS = 300

_SPLIT_RE = re.compile(r"[\s_/()\[\]{}·,‧]+")
_TRAILING_DIGITS_RE = re.compile(r"\d+$")
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
# 브랜드성 ASCII 접두(New, THE 등) — 사용자가 흔히 생략하는 부분
_ASCII_PREFIX_RE = re.compile(r"^[a-z0-9]+")


def _normalize(text: str) -> str:
    """매칭용 정규화 — 공백 제거 + 소문자 (표기 변형 흡수)."""
    return re.sub(r"\s+", "", text).lower()


def extract_aliases(file_name: str, title: str = "") -> set[str]:
    """문서명에서 별칭 후보를 추출한다.

    예: "무배당 프로미라이프 New간편간병보험2601 보험약관.pdf"
    → {"무배당", "프로미라이프", "new간편간병보험2601", "new간편간병보험", "보험약관"}
    (변별력 필터는 인덱스 빌드 시 코퍼스 전체를 보고 적용)
    """
    aliases: set[str] = set()
    for source in (file_name, title):
        if not source:
            continue
        base = _EXTENSION_RE.sub("", source)
        for token in _SPLIT_RE.split(base):
            token = token.strip()
            if len(token) < MIN_ALIAS_LENGTH:
                continue
            norm = _normalize(token)
            aliases.add(norm)
            # 말미 숫자(연도·상품코드) 제거판도 별칭으로 (질문은 보통 코드 생략)
            stripped = _TRAILING_DIGITS_RE.sub("", norm)
            if len(stripped) >= MIN_ALIAS_LENGTH:
                aliases.add(stripped)
            # 브랜드성 ASCII 접두 제거판 — "New간편암건강보험"을 "간편암건강보험"으로도
            # 부를 수 있게 (사용자가 접두를 흔히 생략). 변별력 필터가 뒤에서
            # 과매칭 별칭을 걸러주므로 안전하게 추가만 한다.
            deprefixed = _ASCII_PREFIX_RE.sub("", stripped)
            if deprefixed != stripped and len(deprefixed) >= MIN_ALIAS_LENGTH:
                aliases.add(deprefixed)
    return aliases


@dataclass
class EntityMatch:
    """질문 매칭 결과."""

    doc_ids: set[str] = field(default_factory=set)
    aliases: list[str] = field(default_factory=list)


class EntityDocIndex:
    """별칭 → 문서 ID 인덱스. 코퍼스에서 빌드, TTL로 갱신."""

    def __init__(self) -> None:
        self._alias_to_docs: dict[str, set[str]] = {}
        self._built_at: float = 0.0
        self._doc_count: int = 0

    @property
    def is_stale(self) -> bool:
        return (time.monotonic() - self._built_at) > INDEX_TTL_SECONDS

    def build(self, documents: list[dict]) -> None:
        """(id, file_name, title) 목록에서 인덱스를 재구축한다."""
        alias_to_docs: dict[str, set[str]] = {}
        for doc in documents:
            for alias in extract_aliases(doc.get("file_name", ""), doc.get("title", "")):
                alias_to_docs.setdefault(alias, set()).add(doc["id"])

        # 변별력 필터: 과반 문서에 걸리는 별칭 제거 (회사명 등 공통 접두)
        total = max(1, len(documents))
        self._alias_to_docs = {
            alias: docs
            for alias, docs in alias_to_docs.items()
            if len(docs) / total <= MAX_COVERAGE_RATIO
        }
        self._built_at = time.monotonic()
        self._doc_count = len(documents)
        logger.info(
            "entity_index_built",
            documents=len(documents),
            aliases=len(self._alias_to_docs),
        )

    def match(self, query: str) -> EntityMatch:
        """질문에 등장하는 별칭들의 문서 집합을 반환한다.

        결합 규칙 (결정론):
        - **교집합이 비어있지 않으면 교집합** — "A상품 약관"처럼 상품+유형을
          함께 말한 질문은 정확히 그 문서로 좁힌다(최대 정밀).
        - **교집합이 비면 합집합** — "A랑 B 비교"는 서로소 집합이라 자동으로
          양쪽 문서가 모두 포함된다(recall 보장).
        긴 별칭이 매칭되면 그 부분문자열인 짧은 별칭은 중복이라 제외한다.
        """
        norm_query = _normalize(query)
        hits = [a for a in self._alias_to_docs if a in norm_query]
        # 부분문자열 중복 제거: "new간편간병보험2601"이 걸리면 "new간편간병보험"은 흡수
        maximal = [
            a for a in hits
            if not any(a != other and a in other for other in hits)
        ]
        if not maximal:
            return EntityMatch()
        sets = [self._alias_to_docs[a] for a in maximal]
        intersection = set.intersection(*sets)
        doc_ids = intersection if intersection else set().union(*sets)
        return EntityMatch(doc_ids=doc_ids, aliases=sorted(maximal))
