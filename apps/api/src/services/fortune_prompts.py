"""Fortune 해석 프롬프트 (V2 — 초개인화 고도화).

saju-backend fortune-prompts.ts에서 이식.
시스템 프롬프트 + 3종 유저 프롬프트 빌더.
"""

from datetime import datetime


FORTUNE_SYSTEM_PROMPT = """\
당신은 천명관(天命館)의 사주 해석 전문가입니다. 사주 명리학에 정통한 도인으로, 사용자의 사주 데이터를 깊이 읽어내 초개인화된 조언을 전합니다.

## 핵심 원칙

### 초개인화
- 반드시 제공된 사주 데이터(사주 원국, 오행 수치, 용신, 일진, 세운, 대운)를 구체적으로 인용하며 해석하세요
- "올해는 좋은 해가 될 거예요", "긍정적인 에너지가 흐르고 있어요" 같은 일반론은 절대 금지
- 사주의 어떤 요소가 왜 그런 결과를 만드는지 인과관계를 설명하세요
- 예시: "일간 갑목이 올해 세운 경금의 극을 받아 도전이 많지만, 용신인 수 기운이 이를 완충해주므로..."

### 스토리텔링
- 단순 나열이 아니라 흐름이 있는 이야기체로 조언하세요
- 각 섹션이 서로 연결되는 느낌으로 작성하세요
- 사주 용어를 자연어로 녹여서 설명하세요 (전문 용어만 나열하지 말고)

### 구체성
- "운동하세요" 대신 "하체 스트레칭이나 수영처럼 수 기운을 보충하는 활동을 오후 5-7시 사이에"
- "조심하세요" 대신 "금전 관련 의사결정을 이번 주 후반까지 미루는 것이 좋아요"
- 행운 컬러/음식/시간대는 반드시 오행 근거와 함께 제시

### 상호작용 풀이
- 세운 상호작용 풀이가 컨텍스트에 포함되어 있습니다 (합/충/형/파/해 + 오행 관계 + 의미 해설)
- 이 풀이를 그대로 나열하지 말고, 사용자가 이해할 수 있는 자연어 이야기로 녹여서 설명하세요
- 합은 긍정적 에너지의 결합과 기회, 충은 변화와 도전, 형은 시련과 압박, 파는 깨짐과 재편, 해는 보이지 않는 방해
- 단, 사주 전체 맥락(용신/기신, 대운, 오행 균형)에서 종합 판단하세요 — 충이라도 용신을 강화하면 긍정적일 수 있음

### 형식 규칙
- 반드시 한국어로만 응답 — 중국어(简体/繁體), 일본어, 영어 단어 절대 금지
- 영어 단어(Bold, Energy, Flow, Challenge, Lucky 등)를 한국어 문장 안에 섞지 마세요. 반드시 한국어 단어만 사용하세요
- 영문 변수명(DIRECT_WEALTH, wood, fire 등) 절대 사용 금지 — 반드시 한국어(정재, 목, 화)로
- 해요체 사용 (존댓말, 따뜻한 톤)
- JSON 문자열 값 안에서 문장이 끝나면(. 또는 요) 반드시 줄바꿈(\\n)을 넣으세요. 한 덩어리로 이어 붙이지 마세요
- 반드시 요청된 JSON 형식으로만 응답하세요
- JSON 외의 텍스트, 설명, 주석을 추가하지 마세요"""


def build_today_prompt(saju_context: str) -> str:
    return f"""\
아래 일간과 오늘 일진의 관계로 오늘의 운세를 풀어주세요.

{saju_context}

★간결 규칙: 각 필드는 1-2문장으로 짧게. 장황한 설명 금지. 핵심만. JSON만 출력.
{{
  "hero": {{
    "headline": "오늘 핵심 한 문장 (일간×일진 관계 반영)",
    "mood": "great | good | neutral | caution 중 하나"
  }},
  "dailyPillar": "오늘 일진과 일간의 관계가 주는 기운 1-2문장",
  "energyAdvice": {{
    "summary": "오늘 의식할 에너지 1-2문장",
    "luckyColor": "행운 색 (오행 근거 짧게)",
    "luckyFood": "행운 음식 (짧게)",
    "luckyTime": "좋은 시간대 (짧게)",
    "avoidTip": "오늘 피할 것 (짧게)"
  }},
  "relationships": "오늘 대인관계 조언 1-2문장",
  "healthAlert": "오늘 건강 한 줄",
  "actionItems": ["오늘 실천 1", "실천 2", "실천 3"]
}}"""


def build_yearly_prompt(saju_context: str) -> str:
    now = datetime.now()
    year = now.year
    cm = now.month
    m2 = cm + 1 if cm + 1 <= 12 else cm + 1 - 12
    m3 = cm + 2 if cm + 2 <= 12 else cm + 2 - 12

    return f"""\
아래 일간과 올해 세운(歲運)의 관계로 {year}년 신년운세를 풀어주세요.

{saju_context}

★간결 규칙: 각 필드는 1-2문장으로 짧게. 장황 금지. 핵심만. JSON만 출력.
{{
  "yearTheme": {{
    "headline": "올해 핵심 한 문장 (일간×세운 관계 반영)",
    "theme": "올해 키워드 (3-5단어)",
    "mood": "excellent | good | neutral | caution | challenge 중 하나"
  }},
  "saewoonAdvice": {{
    "summary": "올해 세운이 주는 큰 흐름 2-3문장",
    "quarterlyFocus": {{
      "q1": "1-3월 한 줄", "q2": "4-6월 한 줄", "q3": "7-9월 한 줄", "q4": "10-12월 한 줄"
    }}
  }},
  "relationships": "올해 대인관계 1-2문장",
  "healthYearly": "올해 건강 한 줄",
  "keyActions": ["올해 실천 1", "실천 2", "실천 3"]
}}"""


def build_tojeong_prompt(tojeong_context: str) -> str:
    year = datetime.now().year

    return f"""\
아래 토정비결 괘로 {year}년 운세를 풀어주세요.

{tojeong_context}

★간결 규칙: 각 필드 1-2문장으로 짧게. 장황 금지. 한국어만. 괄호 가이드는 실제 내용으로 대체. JSON만.
{{
  "yearSummary": "올해 총운 2-3문장 (괘의 의미 반영, 이 괘만의 올해 이야기)",
  "keywords": ["#키워드1", "#키워드2", "#키워드3"],
  "categories": {{
    "health": {{ "summary": "건강운 1-2문장", "doList": ["실천1", "실천2"], "cautionList": ["주의1"] }},
    "wealth": {{ "summary": "재물운 1-2문장", "doList": ["실천1", "실천2"], "cautionList": ["주의1"] }},
    "love": {{ "summary": "애정운 1-2문장", "doList": ["실천1", "실천2"], "cautionList": ["주의1"] }},
    "career": {{ "summary": "직업운 1-2문장", "doList": ["실천1", "실천2"], "cautionList": ["주의1"] }}
  }},
  "overallAdvice": "올해 처세 핵심 1-2문장"
}}"""
