"""사주 리포트 V2 섹션별 프롬프트.

ai-worker prompts.py에서 V2 paper/compatibility 섹션 프롬프트를 복사.
LangChain 의존 없이 순수 문자열 기반으로 재구현.
"""

from __future__ import annotations

# ──────────────────────────────────────────────
# Paper V2: 7개 통합 섹션 지침
# ──────────────────────────────────────────────

_PAPER_SECTION_INSTRUCTIONS: dict[str, str] = {
    "sajuWonguk": (
        "[sajuWonguk] (사주 원국 = pillars + interactions)\n"
        "- pillarsHanja: 년/월/일/시 한자 표기\n"
        "- fourPillars: 년/월/일/시 천간·지지·갑자 인덱스\n"
        "- interactions: 합충형해 배열\n"
        "- gender: male/female\n"
        "- llmText.summary: 일간의 성격적 특징과 원국 핵심 구조 2-3문장 (일간 한자 명시)\n"
        "- llmText.advice: 원국 구조 기반 삶의 방향 조언 1-2문장\n"
        "- llmText.characteristics: 일주 조합 성격/기질 서술 3-4문장"
    ),
    "ohangYongsin": (
        "[ohangYongsin] (오행 & 용신 = energy + luckyElements)\n"
        "- energyScore: 오행(wood/fire/earth/metal/water) 수치(0-100)\n"
        "- yongsin: 용신, 희신, 기신, 전략, 점수\n"
        "- llmText.summary: 오행 균형 상태와 용신 선정 근거 2-3문장\n"
        "- llmText.advice: 용신 기반 구체적 개운법(색상, 방향, 직업 등) 2문장"
    ),
    "tenGodsShinsal": (
        "[tenGodsShinsal] (십성 & 신살 = tenGods + shinsal + noblePeople)\n"
        "- tenGods: 십성 배열 (key, isRooted, rootStrength)\n"
        "- shinsal: 신살 명칭 문자열 배열\n"
        "- shinsalByPillar: 년/월/일/시 각 pillar의 신살 정보\n"
        "- noblePeople: 귀인 관련 신살 문자열 배열\n"
        "- llmText.summary: 십성 분포에서 드러나는 사회적 역량/성향 2-3문장\n"
        "- llmText.advice: 신살과 귀인을 활용한 실천 조언 1-2문장"
    ),
    "daewoonFlow": (
        "[daewoonFlow] (대운 흐름 = daewoon + samjae)\n"
        "- daewoon: DaewoonPeriod 배열 (startAge, endAge, gapja, tenGod)\n"
        "- currentAge: 현재 나이\n"
        "- samjae: 삼재 여부, 유형, 연도, 설명\n"
        "- saewoon: 올해 세운 분석\n"
        "- llmText.summary: 현재 대운의 핵심 기운과 삶에 미치는 영향 2-3문장\n"
        "- llmText.advice: 현재 대운 시기에 맞는 전략적 조언 1-2문장"
    ),
    "loveRelation": (
        "[loveRelation] (연애 & 관계운)\n"
        "- basic: 사주 기본 정보\n"
        "- tenGods: TenGodData 배열\n"
        "- shinsal: 연애 관련 신살 (도화살, 홍염살 등)\n"
        "- energyScore: 오행 에너지 점수\n"
        "- llmText.summary: 연애 스타일과 배우자 인연의 특징 2-3문장\n"
        "- llmText.advice: 관계 개선을 위한 구체적 조언 1-2문장\n"
        "- llmText.characteristics: 연애에서의 성격적 특징 2-3문장"
    ),
    "careerWealth": (
        "[careerWealth] (직업 & 재물운 = career + wealth)\n"
        "- tenGods: TenGodData 배열\n"
        "- energyScore: 오행 에너지 점수\n"
        "- wealthFortune: 재물 유형, 지출 성향, 유리한 시기\n"
        "- dayHeavenlyStem: 일간 천간 한자\n"
        "- llmText.summary: 직업 적성과 재물 흐름의 핵심 2-3문장\n"
        "- llmText.advice: 유리한 직업 분야와 재물 관리 전략 1-2문장"
    ),
    "verdictV2": (
        "[verdictV2] (종합 제언 - 전체 섹션 통합)\n"
        "중요: 앞 6개 섹션 분석 결과를 모두 종합하여 작성합니다.\n"
        "[이전 섹션 분석 결과 요약]이 제공되면 반드시 반영하세요.\n"
        "일반론 금지. 반드시 이 사람의 사주 데이터를 구체적으로 언급하세요.\n"
        "- llmText.summary: 이 사주의 핵심 통찰 4-5문장\n"
        "- llmText.advice: 현재 나이와 대운 기반 시기별 행동 전략 3-4문장"
    ),
}

PAPER_V2_SECTION_KEYS: list[str] = list(_PAPER_SECTION_INSTRUCTIONS.keys())


# ──────────────────────────────────────────────
# Compatibility V4: 6개 섹션 지침
# ──────────────────────────────────────────────

_COMPAT_V4_SECTION_INSTRUCTIONS: dict[str, str] = {
    "pillarsV4": (
        "[pillarsV4] (사주 원국 비교)\n"
        "- 두 사람의 일간 관계(합/충/상생/상극) 해석\n"
        "- 사주 원국 구조의 상호 보완성 분석\n"
        "- llmText.summary: 두 사람의 일간 관계와 원국 구조 핵심 해석 3-4문장\n"
        "- llmText.advice: 원국 차이에서 오는 관계 조화법 1-2문장"
    ),
    "energyV4": (
        "[energyV4] (오행 에너지 궁합)\n"
        "- 두 사람의 오행 분포 비교, 과잉/부족 상호 보완성\n"
        "- 용신이 상대에게 어떤 에너지를 제공하는지\n"
        "- llmText.summary: 오행 균형 관점에서 본 궁합 핵심 2-3문장\n"
        "- llmText.advice: 에너지 밸런스를 위한 실천 조언 1-2문장"
    ),
    "tenGodsShinsalV4": (
        "[tenGodsShinsalV4] (십성 & 신살)\n"
        "- 두 사람의 십성 상호작용 해석\n"
        "- 주요 신살의 궁합적 의미\n"
        "- llmText.summary: 십성 조합과 신살에서 드러나는 관계 역학 3-4문장\n"
        "- llmText.advice: 십성/신살 기반 관계 개선 전략 1-2문장"
    ),
    "loveStrengthsV4": (
        "[loveStrengthsV4] (연애 스타일 & 강점)\n"
        "- 두 사람의 연애 패턴, 애정 표현 방식 비교\n"
        "- 관계의 강점과 약점, 성장 포인트\n"
        "- llmText.summary: 연애 스타일 궁합과 관계 강점/약점 3-4문장\n"
        "- llmText.advice: 관계에서 서로를 이해하고 성장하는 방법 1-2문장"
    ),
    "fortuneV4": (
        "[fortuneV4] (운세 흐름)\n"
        "- 두 사람의 현재 대운 비교, 대운 시너지/충돌\n"
        "- 올해 세운의 궁합적 영향\n"
        "- llmText.summary: 현재 및 향후 운세 흐름에서 본 궁합 타이밍 3-4문장\n"
        "- llmText.advice: 현재 시기에 맞는 관계 전략 1-2문장"
    ),
    "verdictV4": (
        "[verdictV4] (종합 운명 판정)\n"
        "중요: 앞 5개 섹션 분석 결과를 모두 종합합니다.\n"
        "[이전 섹션 분석 결과 요약]이 제공되면 반드시 반영하세요.\n"
        "일반론 금지. 반드시 두 사람의 구체적 사주 데이터를 언급하세요.\n"
        "- llmText.summary: 이 궁합의 핵심 통찰 4-5문장\n"
        "- llmText.advice: 두 사람이 함께 성장하기 위한 구체적 행동 전략 2-3문장\n"
        "- llmText.characteristics: 이 궁합의 고유한 운명적 특징 2-3문장 (선택)"
    ),
}

COMPAT_V4_SECTION_KEYS: list[str] = list(_COMPAT_V4_SECTION_INSTRUCTIONS.keys())


# ──────────────────────────────────────────────
# 공통 시스템 프롬프트 빌더
# ──────────────────────────────────────────────

_COMMON_RULES = (
    "규칙:\n"
    '1. 반드시 순수 JSON만 출력. 마크다운 코드블록(```) 금지.\n'
    '2. 출력 형식: {"summary": "...", "advice": "...", '
    '"conclusion": "...", "characteristics": "..."}\n'
    "3. summary (필수): 해당 사용자의 실제 사주 데이터를 1개 이상 명시하는 해석. 200-400자.\n"
    "4. advice (필수): 실천 가능한 구체적 조언. 100-200자.\n"
    "5. conclusion (필수): 이 섹션의 핵심 결론 한 문장. 30-60자.\n"
    "6. characteristics (선택): 성격/기질 서술. 있으면 200-300자.\n"
    "7. 순수 한국어. 볼드 강조 시 **마크다운 볼드** 사용.\n"
    "8. 영문 변수명(DIRECT_WEALTH 등)을 값으로 사용하지 말 것.\n"
    "9. 전문 용어를 일반인이 이해할 수 있게 풀어 쓸 것.\n"
)


def get_paper_section_prompt(section_key: str) -> tuple[str, str]:
    """Paper 단일 섹션의 (system, user) 프롬프트를 반환한다.

    Returns:
        (system_prompt, user_prompt) ��플
    """
    instruction = _PAPER_SECTION_INSTRUCTIONS.get(section_key, "")

    system = (
        "당신은 정통 명리학자입니다. "
        "사주 데이터를 분석하여 아래 섹션의 llmText를 JSON으로 생성하세요.\n\n"
        f"{_COMMON_RULES}\n"
        f"[현재 섹션: {section_key}]\n"
        f"{instruction}\n"
    )

    user = (
        "[사주 원천 데이터]\n"
        "{context_str}\n\n"
        f"위 데이터를 바탕으로 [{section_key}] 섹션의 llmText JSON을 출력하세요.\n"
        "반드시 summary와 advice를 포함하세요. 전문 용어는 일반인이 이해할 수 있게 풀어쓰세요."
    )

    return system, user


def get_compat_section_prompt(section_key: str) -> tuple[str, str]:
    """Compatibility 단일 섹션의 (system, user) 프롬프트를 반환한다.

    Returns:
        (system_prompt, user_prompt) 튜플
    """
    instruction = _COMPAT_V4_SECTION_INSTRUCTIONS.get(section_key, "")

    system = (
        "당신은 정통 명리학자입니다. "
        "두 사람의 사주 데이터를 비교 분석하여 아래 섹션의 llmText를 JSON으로 생성하세요.\n\n"
        f"{_COMMON_RULES}\n"
        f"[현재 섹션: {section_key}]\n"
        f"{instruction}\n"
    )

    user = (
        "[두 사람의 사주 원천 데이터]\n"
        "{context_str}\n\n"
        f"위 데이터를 바탕으로 [{section_key}] 섹션의 llmText JSON을 출력하세요.\n"
        "반드시 summary와 advice를 포함하세요."
    )

    return system, user
