"""리랭커 관련도 floor 튜닝 프로브 — 실제 RAGSearchTool 경로(쿼리 확장 포함)로
인덱싱된 코퍼스(간병·암·실손 보험)에 복잡한 질문 배터리를 돌려 rerank_top_score
분포를 측정한다. LLM 생성(최종 답변)은 하지 않지만, 검색·확장·리랭킹은 라이브와 동일.

쿼리 확장(멀티쿼리)이 약한 질문의 후보셋을 바꿔 top 리랭커 점수를 끌어올리므로,
확장 없는 단순 프로브로는 floor를 과소 보정한다 — 반드시 이 경로로 측정한다.
"""

import asyncio

from src.config import Settings
from src.domain.agent_context import AgentContext
from src.domain.models import SearchScope, SecurityLevel
from src.infrastructure.providers.factory import ProviderFactory
from src.infrastructure.vector_store import VectorStore
from src.tools.internal.rag_search import RAGSearchTool

BATTERY = [
    ("R1", "answer", "참좋은더보장 간병보험에서 장기요양 1등급 받으면 간병자금 얼마 나오고 재가급여랑 시설급여 차이 있어?"),
    ("R2", "answer", "New간편암건강보험에서 유사암이랑 일반암 진단자금 차이가 뭐고 재진단암은 몇 년 지나야 다시 받아?"),
    ("R3", "answer", "유병력자 실손 가입했는데 통원치료 비급여 도수치료 받으면 보상돼? 자기부담금은 얼마야?"),
    ("R4", "answer", "간병보험이랑 암보험 둘 다 있는데 치매로 요양원 들어가면 어느 쪽에서 보장받아?"),
    ("R5", "answer", "간편고지 3개월 5년 고지했는데 고지 안 한 병력 나중에 발견되면 보험금 못 받아?"),
    ("R6", "answer", "암보험 보험료 납입면제 조건이 뭐야? 진단받으면 이후 보험료 안 내도 돼?"),
    ("R7", "answer", "간병보험 보장 개시일이랑 면책기간이 어떻게 돼? 가입하고 바로 청구 가능해?"),
    ("X1", "refuse", "내가 횡단보도를 건너가다가 우회전하는 차량에 치었어 어떻게 해야해?"),
    ("X2", "refuse", "자동차 접촉사고 났는데 대물배상 한도 얼마까지 돼?"),
    ("X3", "refuse", "우리집 화재나서 가전제품 다 탔는데 화재보험으로 얼마 받아?"),
    ("X4", "refuse", "해외여행 가는데 여행자보험 항공기 지연 보상 되나?"),
    ("X5", "refuse", "삼성전자 주식 지금 사도 될까? 배당은 얼마야?"),
    ("X6", "refuse", "강아지가 아파서 동물병원 갔는데 펫보험 청구 어떻게 해?"),
    ("B1", "?", "실비보험으로 임플란트 치료비 보장돼?"),
    ("B2", "?", "보험 해지하면 환급금 얼마나 받아?"),
]


async def main():
    import os
    from src.locale.bundle import LocaleBundle, set_locale

    s = Settings()
    _src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    set_locale(LocaleBundle.load(os.path.join(_src, "src", "locale", f"{s.locale}.yaml")))

    import sys
    # 도메인 인자화 — 프로필의 domain_scopes에 맞춰 코퍼스를 좁혀 측정한다.
    # 사용: python -m scripts.probe_rerank_floor [도메인코드 ...]  (기본: 보험)
    domain_codes = sys.argv[1:] or ["보험"]

    factory = ProviderFactory(s)
    store = VectorStore(s.database_url)
    await store.connect()
    # min_rerank_score=0.0 → floor 미적용 raw top 관측. router_llm=orchestration_llm(확장용).
    tool = RAGSearchTool(
        embedding_provider=factory.get_embedding_provider(),
        vector_store=store,
        reranker=factory.get_reranker(),
        router_llm=factory.get_orchestration_llm(),
        min_rerank_score=0.0,
    )
    print(f"domain_codes = {domain_codes}")
    scope = SearchScope(
        domain_codes=domain_codes, security_level_max=SecurityLevel.INTERNAL, tenant_id="default",
    )
    ctx = AgentContext(session_id="probe", tenant_id="default")

    print(f"floor(config) = {s.rag_min_rerank_score}\n")
    print(f"{'ID':4}{'기대':8}{'top':>9}{'final':>7}  질문")
    rows = []
    for label, expect, q in BATTERY:
        res = await tool.execute({"query": q, "max_vector_chunks": 8}, ctx, scope)
        data = res.data or []
        top = max((r.get("rerank_score", 0.0) for r in data), default=0.0)
        rows.append((label, expect, top))
        print(f"{label:4}{expect:8}{top:>9.4f}{len(data):>7}  {q[:28]}")

    await store.close()

    rel = sorted(t for _l, e, t in rows if e == "answer")
    irr = sorted(t for _l, e, t in rows if e == "refuse")
    print("\n=== 분리 분석 (라이브 경로: 쿼리 확장 포함) ===")
    print(f"관련(answer) 정렬: {[round(x, 4) for x in rel]}")
    print(f"무관(refuse) 정렬: {[round(x, 4) for x in irr]}")
    if rel and irr:
        print(f"무관 최댓값={max(irr):.4f}  관련 최솟값={min(rel):.4f}", end="  ")
        if min(rel) > max(irr):
            print(f"→ 깨끗한 분리, 권장 floor≈{(max(irr) + min(rel)) / 2:.4f}")
        else:
            # 오버랩: floor별 오분류 집계
            print("→ 겹침. floor 후보별 (무관누수, 관련반려):")
            for f in [0.55, 0.57, 0.58, 0.59, 0.60, 0.62, 0.64]:
                leak = sum(1 for t in irr if t >= f)      # 무관인데 통과(누수)
                miss = sum(1 for t in rel if t < f)       # 관련인데 반려
                print(f"    floor={f:.2f}: 무관누수={leak}/{len(irr)}  관련반려={miss}/{len(rel)}")


if __name__ == "__main__":
    asyncio.run(main())
