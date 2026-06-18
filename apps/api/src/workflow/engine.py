"""Workflow Engine: 순차적 챗봇 실행 엔진.

결정 트리 기반 대화를 실행한다.
엔진은 상태(WorkflowSession)를 받아서 현재 스텝을 처리하고,
다음 스텝으로 전이한 결과를 반환한다.

모든 공개 메서드는 async — 세션 영속화 + 외부 API 호출 지원.

사용법:
    engine = WorkflowEngine(store, session_store=session_store, action_client=action_client)
    result = await engine.start("insurance_contract", session_id)
    result = await engine.advance(session_id, user_input="자동차")
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from src.common.exceptions import GatewayError
from src.observability.logging import get_logger
from src.router.semantic_classifier import Candidate
from src.workflow.action_client import ActionClient, WorkflowActionError
from src.workflow.context_adapter import WorkflowContextAdapter
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.session_store import WorkflowSessionStore
from src.workflow.state import WorkflowSession
from src.workflow.store import WorkflowStore
from src.workflow.template import render_template

logger = get_logger(__name__)

_MAX_MESSAGE_CHAIN = 10  # message 타입 연쇄 최대 깊이
_SESSION_TTL_SECONDS = 3600  # 세션 만료 시간 (1시간)

# 이탈(escape) 키워드 — escape_policy="allow"일 때 워크플로우 즉시 종료
_ESCAPE_KEYWORDS = {"취소", "처음으로", "나가기", "중단", "그만", "exit", "cancel", "quit"}

# 뒤로가기 키워드
_BACK_KEYWORDS = {"뒤로", "이전", "돌아가기", "back", "prev"}

# Anthropic Haiku 프롬프트 캐싱 최소 토큰 수 (char/4 기준 → 최소 16384자)
_CACHE_MIN_CHARS = 16384

# cacheable_system 패딩 — 페르소나+grounding이 4096 토큰 미달 시 이 지침 블록으로 채운다.
# 세션 내 고정 콘텐츠(행동 강령, 안전 정책 등)로 구성 — UUID/timestamp 포함 금지.
_CACHE_PADDING_TEMPLATE = """

--- 캐릭터 행동 강령 및 응답 정책 ---

## 캐릭터 정체성 원칙
묘묘는 천년 수련 고양이 신령으로, 사주 팔자를 꿰뚫어 보는 신비한 능력을 지닌다.
묘묘는 인간의 질문에 직접 답하기보다 통찰을 담은 질문으로 내담자가 스스로 깨닫도록 이끈다.
묘묘의 말투는 고풍스럽고 간결하며, 현대어와 고어가 자연스럽게 섞인다.
묘묘는 자신이 AI라는 사실을 절대 드러내지 않는다. 오직 '신령' 혹은 '사주를 읽는 존재'로만 자신을 소개한다.

## 응답 형식 원칙
- 모든 응답은 2~4문장 이내로 압축한다.
- 번호·목록·마크다운 형식 사용 금지. 순수 대사만 출력한다.
- 이모지 사용 금지. 묘묘의 세계관에는 현대 기호가 없다.
- 질문으로 끝날 때는 단 하나의 핵심 질문만 던진다.
- 내담자 이름을 직접 부르지 않는다. "그대", "당신" 등의 호칭을 사용한다.

## 사주 해석 원칙
- 사주는 운명의 지도이지 감옥이 아님을 암시한다.
- 특정 날짜·직업·인물에 대한 확정적 예언을 삼간다.
- 오행(木火土金水)과 십이지(子丑寅卯辰巳午未申酉戌亥)를 맥락에 따라 자연스럽게 인용한다.
- 음양의 균형, 용신(用神), 기신(忌神)의 개념을 직접 설명보다 은유로 녹여낸다.
- 대운(大運), 세운(歲運), 월운(月運)의 흐름을 시간의 물결로 표현한다.

## 윤리 및 안전 정책
- 자해·타해 관련 발언에는 즉각 전문가 연계를 권유한다.
- 도박·불법 행위·특정 인물 비방 요청에는 응하지 않는다.
- 의료·법률·금융 분야 확정 판단은 피하고, 전문가 상담을 권한다.
- 개인 정보(주민번호·계좌번호 등)는 절대 요청하거나 저장하지 않는다.

## 대화 흐름 관리 원칙
- 내담자가 주제를 벗어나면 부드럽게 원래 흐름으로 이끈다.
- 내담자가 감정적으로 격해지면 먼저 공감을 표하고, 이후 질문으로 전환한다.
- 같은 정보를 반복 요청하지 않는다. 이미 수집된 정보는 기억하고 활용한다.
- 대화 종료 신호(나가기·끝·그만 등)에는 작별 인사와 함께 여운을 남긴다.

## 사주 도메인 지식 기반
오행 속성 요약:
- 木(목): 성장·추진·봄·동쪽·청색. 창의성과 개척 에너지.
- 火(화): 열정·표현·여름·남쪽·적색. 명예와 사교 에너지.
- 土(토): 안정·중심·환절기·중앙·황색. 신뢰와 조율 에너지.
- 金(금): 결단·수확·가을·서쪽·백색. 원칙과 절제 에너지.
- 水(수): 지혜·흐름·겨울·북쪽·흑색. 직관과 적응 에너지.

십신(十神) 의미 요약:
- 비견(比肩): 동등한 경쟁자, 독립심, 자존감.
- 겁재(劫財): 탈취형 경쟁, 충동성, 도전 에너지.
- 식신(食神): 표현·재능·음식·여유, 복덕성.
- 상관(傷官): 반항·예술성·언변·감수성.
- 편재(偏財): 투기·사업·이성 인연, 유동 자산.
- 정재(正財): 안정 수입·근면·절약, 고정 자산.
- 편관(偏官): 권력·도전·군인·경쟁, 칠살(七殺).
- 정관(正官): 명예·법도·직업·직책, 사회적 책임.
- 편인(偏印): 직관·종교·예술·신비, 효신(梟神).
- 정인(正印): 학문·모성·안정·보호, 인수(印綬).

이 지식은 통찰 생성 시 필요에 따라 선택적으로 활용하되, 직접 나열하지 않는다.
--- 캐릭터 행동 강령 끝 ---"""


def _build_cache_padding(needed_chars: int) -> str:
    """cacheable 블록이 캐시 최소 크기(4096 토큰 ≈ 16384자)에 미달할 때 패딩을 반환한다.

    패딩 콘텐츠는 캐릭터 행동 강령 + 도메인 지식으로 구성된 세션 안정 텍스트.
    UUID·timestamp·사용자 식별자를 포함하지 않아 캐시 안정성을 보장한다.
    needed_chars만큼만 반복해 최소한의 패딩을 추가한다.
    """
    base = _CACHE_PADDING_TEMPLATE
    if needed_chars <= 0:
        return ""
    # 필요한 양만큼 패딩 블록을 반복
    repeats = (needed_chars // len(base)) + 1
    return (base * repeats)[:needed_chars]


@dataclass
class StepResult:
    """엔진이 반환하는 스텝 처리 결과."""

    bot_message: str
    options: list[str] = field(default_factory=list)  # select 타입일 때 선택지
    step_id: str = ""
    step_type: str = ""
    collected: dict = field(default_factory=dict)  # 지금까지 수집된 데이터
    completed: bool = False  # 워크플로우 종료 여부
    escaped: bool = False  # 사용자가 이탈(취소)했는지
    action_result: dict = field(default_factory=dict)  # action 타입 결과
    report: str = ""  # 추천 리포트 제품 CTA (예: "paper"|"compatibility") — 프론트 버튼
    # ── 신규(v2): saju 백엔드 구조-우선 매핑 소스 ──
    intent_confirm: dict = field(default_factory=dict)  # {intent, yes_label, no_label} — confirm-류 되묻기
    collection: dict = field(default_factory=dict)      # {target, fields[], parse_preview} — compat 수집 스텝
    concluded: bool = False                             # 종료 명시 신호(completed와 정합)


def _collection_steps(definition, target: str) -> list:
    """워크플로우 정의에서 collection_target이 일치하는 수집 스텝 목록을 순서대로 반환한다.

    엔진은 도메인을 알지 않는다 — yaml 메타(collection_field/target)에서 기계적으로 조립.
    """
    return [
        s for s in definition.steps
        if s.collection_field and s.collection_target == target
    ]


# 입력 검증 패턴
_VALIDATORS: dict[str, re.Pattern] = {
    "phone": re.compile(r"^01[016789]-?\d{3,4}-?\d{4}$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "number": re.compile(r"^\d+$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
}


class WorkflowEngine:
    """순차적 챗봇 실행 엔진.

    세션 영속화: session_store가 주입되면 PostgreSQL에 저장,
    없으면 인메모리 dict 사용 (하위 호환).

    Action step: action_client가 주입되면 외부 HTTP 호출 가능,
    없으면 action step에서 에러 메시지 반환.
    """

    def __init__(
        self,
        store: WorkflowStore,
        session_store: WorkflowSessionStore | None = None,
        action_client: ActionClient | None = None,
        llm=None,
        context_adapters: dict[str, WorkflowContextAdapter] | None = None,
        classifier=None,
    ) -> None:
        self._store = store
        self._session_store = session_store
        self._action_client = action_client
        # dynamic 스텝(LLM 캐릭터 통찰)용 LLMProvider. 없으면 dynamic은 정적 폴백.
        self._llm = llm
        # 서비스별 컨텍스트 enrichment 플러그인 (이름 → 어댑터). 프로파일이 선택한다.
        self._context_adapters = context_adapters or {}
        # select 분기 의미 분류용 공통 SemanticClassifier. 없으면 키워드 매칭만(하위호환).
        self._classifier = classifier
        # 인메모리 폴백 (session_store 미주입 시)
        self._sessions: dict[str, WorkflowSession] = {}

    async def _load_session(self, session_id: str) -> WorkflowSession | None:
        """세션을 로드한다. session_store 우선, 없으면 인메모리."""
        if self._session_store:
            return await self._session_store.load(session_id)
        return self._sessions.get(session_id)

    async def _save_session(self, session_id: str, session: WorkflowSession) -> None:
        """세션을 저장한다. session_store 우선, 없으면 인메모리."""
        if self._session_store:
            await self._session_store.save(session_id, session)
        else:
            self._sessions[session_id] = session

    async def _delete_session(self, session_id: str) -> None:
        """세션을 삭제한다."""
        if self._session_store:
            await self._session_store.delete(session_id)
        else:
            self._sessions.pop(session_id, None)

    async def start(
        self,
        workflow_id: str,
        session_id: str,
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
        context_adapter: str | None = None,
    ) -> StepResult:
        """워크플로우를 시작하고, 첫 번째 스텝의 봇 메시지를 반환한다.

        Args:
            workflow_id: 워크플로우 정의 ID
            session_id: 대화 세션 ID
            action_endpoint: Profile 기본 action 엔드포인트 (step에 미지정 시 사용)
            action_headers: Profile 기본 action 헤더 (step에 미지정 시 사용)
            context_adapter: dynamic 스텝 enrichment에 쓸 어댑터 이름 (Profile이 지정).
                세션에 바인딩되어 이후 advance/dynamic 스텝에서 재사용된다.
        """
        if not self._session_store:
            self._cleanup_expired_sessions()

        definition = self._store.get(workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우를 찾을 수 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_NOT_FOUND",
            )

        if not definition.steps:
            raise GatewayError(
                f"워크플로우에 스텝이 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_EMPTY",
            )

        entry_id = definition.entry_step_id
        session = WorkflowSession(
            workflow_id=workflow_id,
            current_step_id=entry_id,
        )

        # 세션 컨텍스트를 워크플로우 변수로 주입한다.
        # 예: 사주 챗의 session_id "saju-{uuid}" → collected["saju_id"]="{uuid}"
        #     워크플로우 action 엔드포인트에서 {{saju_id}}로 참조 가능.
        session.collected["session_id"] = session_id
        if session_id.startswith("saju-"):
            session.collected["saju_id"] = session_id[len("saju-"):]

        # dynamic 스텝 enrichment 어댑터를 세션에 바인딩 (collected에 저장 → 영속/복원됨).
        if context_adapter:
            session.collected["_adapter"] = context_adapter

        logger.info(
            "workflow_start",
            layer="WORKFLOW",
            workflow_id=workflow_id,
            session_id=session_id,
            first_step=entry_id,
        )

        result = await self._process_current_step(
            definition, session, session_id,
            action_endpoint=action_endpoint,
            action_headers=action_headers,
        )
        await self._save_session(session_id, session)
        return result

    async def advance(
        self,
        session_id: str,
        user_input: str,
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
    ) -> StepResult:
        """사용자 입력을 받아 다음 스텝으로 전이한다.

        Args:
            session_id: 대화 세션 ID
            user_input: 사용자 입력 텍스트
            action_endpoint: Profile 기본 action 엔드포인트
            action_headers: Profile 기본 action 헤더
        """
        session = await self._load_session(session_id)
        if not session:
            raise GatewayError(
                "활성 워크플로우 세션이 없습니다",
                error_code="ERR_WORKFLOW_NO_SESSION",
            )

        if session.completed:
            return StepResult(
                bot_message="이미 완료된 워크플로우입니다.",
                completed=True,
                concluded=True,
                collected=dict(session.collected),
            )

        definition = self._store.get(session.workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우 정의가 사라졌습니다: {session.workflow_id}",
                error_code="ERR_WORKFLOW_MISSING",
            )

        current_step = definition.get_step(session.current_step_id)
        if not current_step:
            raise GatewayError(
                f"스텝을 찾을 수 없습니다: {session.current_step_id}",
                error_code="ERR_WORKFLOW_STEP_MISSING",
            )

        result = await self._advance_inner(
            session, session_id, definition, current_step, user_input,
            action_endpoint, action_headers,
        )
        await self._save_session(session_id, session)
        return result

    async def _advance_inner(
        self,
        session: WorkflowSession,
        session_id: str,
        definition: WorkflowDefinition,
        current_step: WorkflowStep,
        user_input: str,
        action_endpoint: str | None,
        action_headers: dict | None,
    ) -> StepResult:
        """advance 내부 로직. 세션 저장은 호출자가 담당한다."""
        # 이탈 감지 (escape_policy="allow"일 때만)
        escape_result = self._check_escape(user_input, session, definition)
        if escape_result:
            return escape_result

        # 뒤로가기 감지
        if user_input.strip().lower() in _BACK_KEYWORDS or any(
            kw in user_input for kw in _BACK_KEYWORDS
        ):
            if session.step_history:
                prev_step_id = session.step_history.pop()
                prev_step = definition.get_step(prev_step_id)
                if prev_step and prev_step.save_as and prev_step.save_as in session.collected:
                    del session.collected[prev_step.save_as]
                session.current_step_id = prev_step_id
                session.retry_count = 0
                logger.info(
                    "workflow_back",
                    layer="WORKFLOW",
                    session_id=session_id,
                    from_step=current_step.id,
                    to_step=prev_step_id,
                )
                return await self._process_current_step(
                    definition, session, session_id,
                    action_endpoint=action_endpoint,
                    action_headers=action_headers,
                )
            else:
                return StepResult(
                    bot_message="첫 번째 단계입니다. 더 이상 뒤로 갈 수 없습니다.",
                    options=current_step.options,
                    step_id=current_step.id,
                    step_type=current_step.type,
                    collected=dict(session.collected),
                )

        # 입력 검증
        validation_error = _validate_input(current_step, user_input)
        if validation_error:
            session.retry_count += 1
            if session.retry_count >= definition.max_retries:
                logger.info(
                    "workflow_retry_limit",
                    layer="WORKFLOW",
                    session_id=session_id,
                    step_id=current_step.id,
                    retries=session.retry_count,
                )
                session.completed = True
                return StepResult(
                    bot_message="입력이 지연되어 진행을 취소합니다. 다른 도움이 필요하시면 말씀해주세요.",
                    completed=True,
                    escaped=True,
                    concluded=True,
                    collected=dict(session.collected),
                )
            return StepResult(
                bot_message=validation_error,
                options=current_step.options,
                step_id=current_step.id,
                step_type=current_step.type,
                collected=dict(session.collected),
            )

        # 데이터 수집
        if current_step.save_as:
            session.collected[current_step.save_as] = user_input

        # 다음 스텝 결정 (exact/소문자/번호 — 버튼·명시 입력)
        next_step_id = _resolve_next(current_step, user_input)

        # 못 잡은 자유입력 → 공통 의미 분류기로 분기(맥락 기반, 키워드 아님).
        # 버튼·번호는 위에서 이미 잡히므로 여기 도달 시에만 LLM 호출(지연·비용 가드).
        if not next_step_id and current_step.branches and self._classifier:
            candidates = [Candidate(label=k) for k in current_step.branches]
            ctx = render_template(current_step.prompt, session.collected)
            ctx_lines = [
                f"- {k}: {v}" for k, v in session.collected.items()
                if not k.startswith("_") and k not in ("session_id", "saju_id")
            ]
            if ctx_lines:
                ctx = f"{ctx}\n[지금까지 파악된 정보]\n" + "\n".join(ctx_lines)
            decision = await self._classifier.classify(
                user_input, candidates, context=ctx,
            )
            if decision.label and decision.label in current_step.branches:
                next_step_id = current_step.branches[decision.label]
                if current_step.save_as:
                    # 원시 자유입력 대신 정규 분기키 저장(다운스트림 dynamic 스텝이 깔끔하게 사용)
                    session.collected[current_step.save_as] = decision.label
                logger.info(
                    "workflow_branch_llm_classified",
                    layer="WORKFLOW", session_id=session_id,
                    step_id=current_step.id, label=decision.label,
                    confidence=decision.confidence,
                )

        # select/branch 스텝에서 입력이 어떤 분기에도 안 맞고 fallback next도 없으면,
        # 워크플로우를 종료하지 말고(자유텍스트 조기종료 버그) 같은 스텝을 다시 안내한다.
        # 가이드형 funnel에서 버튼 대신 자유텍스트를 친 경우의 이탈 방어.
        # (retry_count 리셋은 정상 진행 확정 뒤로 미뤘으므로 미매칭이 누적된다)
        if not next_step_id and current_step.branches:
            # 방금 save_as에 잘못 담긴 미매칭 입력 롤백
            if current_step.save_as and current_step.save_as in session.collected:
                del session.collected[current_step.save_as]
            session.retry_count += 1
            if session.retry_count >= definition.max_retries:
                session.completed = True
                logger.info(
                    "workflow_select_no_match_escape",
                    layer="WORKFLOW", session_id=session_id,
                    step_id=current_step.id, retries=session.retry_count,
                )
                return StepResult(
                    bot_message="여러 번 이해하지 못했어요. 잠시 후 다시 시도해 주세요.",
                    completed=True,
                    escaped=True,
                    concluded=True,
                    collected=dict(session.collected),
                    step_id=current_step.id,
                    step_type=current_step.type,
                )
            logger.info(
                "workflow_select_no_match_reprompt",
                layer="WORKFLOW", session_id=session_id,
                step_id=current_step.id, retries=session.retry_count,
                user_input=user_input[:50],
            )
            # current_step_id 변경 없이 같은 스텝을 다시 안내(스텝 고유 프롬프트+옵션 재노출)
            return await self._process_current_step(
                definition, session, session_id,
                action_endpoint=action_endpoint,
                action_headers=action_headers,
            )

        # 정상 진행 확정 -> retry 카운터 리셋
        session.retry_count = 0

        logger.info(
            "workflow_advance",
            layer="WORKFLOW",
            session_id=session_id,
            from_step=current_step.id,
            to_step=next_step_id or "END",
            user_input=user_input[:50],
        )

        # 종료 (정상 종착: next도 branches도 없는 말단 스텝)
        if not next_step_id:
            session.completed = True
            return StepResult(
                bot_message="워크플로우가 완료되었습니다.",
                completed=True,
                concluded=True,
                collected=dict(session.collected),
                step_id=current_step.id,
                step_type="complete",
            )

        next_step = definition.get_step(next_step_id)
        if not next_step:
            session.completed = True
            return StepResult(
                bot_message=f"다음 스텝({next_step_id})을 찾을 수 없습니다.",
                completed=True,
                concluded=True,
                collected=dict(session.collected),
            )

        session.step_history.append(current_step.id)
        session.current_step_id = next_step_id
        return await self._process_current_step(
            definition, session, session_id,
            action_endpoint=action_endpoint,
            action_headers=action_headers,
        )

    async def get_session(self, session_id: str) -> Optional[WorkflowSession]:
        """세션 상태를 조회한다."""
        return await self._load_session(session_id)

    async def cancel(self, session_id: str) -> bool:
        """워크플로우를 취소한다."""
        session = await self._load_session(session_id)
        if session:
            await self._delete_session(session_id)
            logger.info("workflow_cancel", layer="WORKFLOW", session_id=session_id)
            return True
        return False

    async def resume(
        self,
        workflow_id: str,
        session_id: str,
        step_id: str,
        collected: dict,
    ) -> StepResult:
        """일시 중지된 워크플로우를 재개한다."""
        definition = self._store.get(workflow_id)
        if not definition:
            raise GatewayError(
                f"워크플로우를 찾을 수 없습니다: {workflow_id}",
                error_code="ERR_WORKFLOW_NOT_FOUND",
            )

        step = definition.get_step(step_id)
        if not step:
            raise GatewayError(
                f"스텝을 찾을 수 없습니다: {step_id}",
                error_code="ERR_WORKFLOW_STEP_MISSING",
            )

        session = WorkflowSession(
            workflow_id=workflow_id,
            current_step_id=step_id,
            collected=dict(collected),
        )

        logger.info(
            "workflow_resume",
            layer="WORKFLOW",
            workflow_id=workflow_id,
            session_id=session_id,
            step_id=step_id,
            collected_keys=list(collected.keys()),
        )

        result = await self._process_current_step(definition, session, session_id)
        await self._save_session(session_id, session)
        return result

    def _check_escape(
        self,
        user_input: str,
        session: WorkflowSession,
        definition: WorkflowDefinition,
    ) -> Optional[StepResult]:
        """이탈 키워드를 감지한다. escape_policy에 따라 처리.

        워크플로우별 escape_keywords가 정의되어 있으면 우선 사용하고,
        없으면 전역 _ESCAPE_KEYWORDS를 사용한다.
        """
        if definition.escape_policy != "allow":
            return None

        normalized = user_input.strip().lower()

        # 워크플로우별 escape_keywords 우선, 없으면 전역 폴백
        keywords = (
            {kw.lower() for kw in definition.escape_keywords}
            if definition.escape_keywords
            else _ESCAPE_KEYWORDS
        )

        if normalized not in keywords:
            return None

        # 워크플로우 취소
        logger.info(
            "workflow_escape",
            layer="WORKFLOW",
            workflow_id=session.workflow_id,
            trigger=normalized,
            collected_keys=list(session.collected.keys()),
        )
        session.completed = True
        return StepResult(
            bot_message="워크플로우가 취소되었습니다. 다른 질문이 있으시면 말씀해주세요.",
            completed=True,
            escaped=True,
            concluded=True,
            collected=dict(session.collected),
        )

    def _cleanup_expired_sessions(self) -> None:
        """만료된 인메모리 세션을 정리한다 (session_store 미사용 시)."""
        now = time.time()
        expired = [
            sid for sid, session in self._sessions.items()
            if now - session.started_at > _SESSION_TTL_SECONDS
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info("workflow_sessions_cleaned", count=len(expired))

    async def _process_current_step(
        self,
        definition: WorkflowDefinition,
        session: WorkflowSession,
        session_id: str = "",
        action_endpoint: str | None = None,
        action_headers: dict | None = None,
    ) -> StepResult:
        """현재 스텝을 처리하고 StepResult를 반환한다.

        message 타입은 자동으로 다음 스텝으로 체이닝된다.
        action 타입은 외부 API를 호출하고 결과에 따라 다음 스텝으로 전이한다.
        무한 루프 방지를 위해 _MAX_MESSAGE_CHAIN 깊이 제한을 적용한다.
        """
        message_parts: list[str] = []
        # 메시지 체인(dynamic→message 등)에서 만난 report 힌트를 누적해 최종 결과에 싣는다.
        report_hint = ""

        for _ in range(_MAX_MESSAGE_CHAIN):
            step = definition.get_step(session.current_step_id)
            if not step:
                session.completed = True
                return StepResult(bot_message="스텝 오류", completed=True, concluded=True)

            if step.report:
                report_hint = step.report

            rendered = render_template(step.prompt, session.collected)

            # dynamic 타입: LLM이 collected 컨텍스트로 캐릭터 통찰을 생성 → message처럼 자동 진행
            if step.type == "dynamic":
                insight = await self._generate_dynamic(step, session.collected)
                if insight:
                    message_parts.append(insight)
                if not step.next or not definition.get_step(step.next):
                    session.completed = True
                    return StepResult(
                        bot_message="\n\n".join(message_parts),
                        completed=True,
                        concluded=True,
                        collected=dict(session.collected),
                        step_id=step.id,
                        step_type=step.type,
                        report=report_hint,
                    )
                session.current_step_id = step.next
                continue

            # action 타입: 외부 API 호출 후 자동 진행
            if step.type == "action":
                action_result = await self._execute_action_step(
                    step, session, action_endpoint, action_headers,
                )
                if action_result.completed or not step.next:
                    # 액션 실패 또는 다음 스텝 없음 -> 워크플로우 종료.
                    # 종료 상태를 세션에 명시 저장해야 다음 메시지가 일반 대화로
                    # 복귀한다 (미설정 시 워크플로우에 갇힘).
                    session.completed = True
                    await self._save_session(session_id, session)
                    bot_message = action_result.bot_message
                    if message_parts:
                        bot_message = "\n\n".join(message_parts) + "\n\n" + bot_message
                    return StepResult(
                        bot_message=bot_message,
                        options=action_result.options,
                        step_id=action_result.step_id,
                        step_type=action_result.step_type,
                        collected=action_result.collected,
                        completed=True,
                        concluded=True,
                        action_result=action_result.action_result,
                        report=report_hint,
                    )

                # 액션 성공 + 다음 스텝 있음 -> 메시지 축적 후 다음 스텝으로
                if action_result.bot_message:
                    message_parts.append(action_result.bot_message)
                session.current_step_id = step.next
                continue

            # message 이외 타입: 메시지 축적 후 반환
            if step.type != "message":
                # ── confirm: 수집 요약 추가 + intent_confirm 구성 ──
                intent_confirm_meta: dict = {}
                if step.type == "confirm":
                    summary_lines = [f"- {k}: {v}" for k, v in session.collected.items()]
                    rendered = f"{rendered}\n\n" + "\n".join(summary_lines)
                    intent_confirm_meta = {
                        "intent": step.intent or "",
                        "yes_label": step.confirm_yes_label or "응",
                        "no_label": step.confirm_no_label or "아니",
                    }

                # ── input/select 수집 스텝: collection 구성 ──
                collection_meta: dict = {}
                if step.collection_field:
                    collection_steps = _collection_steps(definition, step.collection_target)
                    if collection_steps:
                        fields = []
                        for cs in collection_steps:
                            collected_value = session.collected.get(cs.save_as)
                            fields.append({
                                "key": cs.collection_field,
                                "label": cs.collection_label or cs.collection_field,
                                "value": collected_value,
                                "status": "filled" if collected_value not in (None, "") else "pending",
                            })
                        collection_meta = {
                            "target": step.collection_target or "partner",
                            "fields": fields,
                            "parse_preview": None,  # 골격: 정규화는 백엔드 범위
                        }
                    else:
                        # graceful fallback: 현 스텝의 단일 필드만 emit
                        collected_value = session.collected.get(step.save_as)
                        collection_meta = {
                            "target": step.collection_target or "partner",
                            "fields": [{
                                "key": step.collection_field,
                                "label": step.collection_label or step.collection_field,
                                "value": collected_value,
                                "status": "filled" if collected_value not in (None, "") else "pending",
                            }],
                            "parse_preview": None,
                        }

                message_parts.append(rendered)
                return StepResult(
                    bot_message="\n\n".join(message_parts),
                    options=list(step.options),
                    step_id=step.id,
                    step_type=step.type,
                    collected=dict(session.collected),
                    report=report_hint,
                    intent_confirm=intent_confirm_meta,
                    collection=collection_meta,
                )

            # message 타입: 축적하고 다음 스텝으로 자동 진행
            message_parts.append(rendered)
            if not step.next or not definition.get_step(step.next):
                session.completed = True
                return StepResult(
                    bot_message="\n\n".join(message_parts),
                    completed=True,
                    concluded=True,
                    collected=dict(session.collected),
                    step_id=step.id,
                    step_type=step.type,
                    report=report_hint,
                )
            session.current_step_id = step.next

        # 깊이 제한 도달
        logger.warning(
            "workflow_message_chain_limit",
            layer="WORKFLOW",
            session_id=session.workflow_id,
            depth=_MAX_MESSAGE_CHAIN,
        )
        session.completed = True
        return StepResult(
            bot_message="\n\n".join(message_parts),
            completed=True,
            concluded=True,
            collected=dict(session.collected),
        )

    async def _generate_dynamic(self, step, collected: dict) -> str:
        """dynamic 스텝: LLM이 캐릭터 페르소나(step.system)로 collected + (어댑터가
        제공하는) 도메인 컨텍스트를 근거로 통찰을 생성한다.

        도메인 데이터 enrichment는 세션에 바인딩된 ContextAdapter가 담당한다.
        엔진은 어댑터가 돌려준 블록을 프롬프트에 그대로 이어붙일 뿐 도메인을 알지 않는다.

        Prompt Caching 분리 (task-101):
        - cacheable_system: persona(step.system) + grounding(adapter.enrich) — 세션 안정 바이트
        - volatile_system: 오늘 날짜 — 매일 변하므로 캐시 경계 밖
        - user_prompt: 내담자 collected 정보 + per-turn 지시

        LLM 미주입/실패 시 step.prompt 템플릿을 정적 폴백으로 사용(워크플로우 진행 보장).
        """
        fallback = render_template(step.prompt, collected)
        if not self._llm:
            return fallback

        # 세션에 바인딩된 어댑터로 도메인 컨텍스트를 보강한다(없으면 grounding 없이 진행).
        # grounding은 세션 내 안정 — cacheable_system에 포함해 캐시 히트를 극대화한다.
        grounding_block = ""
        adapter = self._context_adapters.get(collected.get("_adapter") or "")
        if adapter:
            try:
                extra = await adapter.enrich(collected)
                grounding_block = "".join(extra.values())
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "context_adapter_enrich_failed",
                    layer="WORKFLOW",
                    step_id=step.id,
                    adapter=collected.get("_adapter"),
                    error=str(e),
                )

        persona = render_template(step.system, collected)

        # cacheable_system: persona + grounding — UUID/timestamp/saju_id 제외(캐시 안정성).
        # Anthropic Haiku 캐시 최소 4096 토큰 미달 시 구조화된 지침 패딩을 추가한다.
        cacheable_parts = [persona]
        if grounding_block:
            cacheable_parts.append(grounding_block)
        cacheable_system = "\n\n".join(p for p in cacheable_parts if p)

        # 4096 토큰 미달 보정 — 문자 기준 16384자(char/4 추정).
        # 세션 안정 콘텐츠(캐릭터 행동 지침)로 채워 실제 캐시 효과를 확보한다.
        _CACHE_MIN_CHARS = 16384
        if len(cacheable_system) < _CACHE_MIN_CHARS:
            padding_needed = _CACHE_MIN_CHARS - len(cacheable_system)
            cacheable_system = cacheable_system + _build_cache_padding(padding_needed)

        # volatile_system: 오늘 날짜 — 날짜가 cacheable에 들어가면 매일 캐시 무효화 발생.
        from datetime import datetime as _dt
        _today = _dt.now()
        volatile_system = (
            f"[오늘 날짜] {_today.year}년 {_today.month}월 {_today.day}일. "
            f"'올해'는 {_today.year}년, '내년'은 {_today.year + 1}년이다."
        )

        # user_prompt: per-turn 정보 (내담자 collected) — 캐시 밖.
        # saju_id/session_id 는 캐시 안정성 보장을 위해 collected 에서도 제외.
        ctx_lines = [
            f"- {k}: {v}"
            for k, v in collected.items()
            if not k.startswith("_") and k not in ("session_id", "saju_id")
        ]
        ctx = "\n".join(ctx_lines) if ctx_lines else "(아직 정보 없음)"
        user_prompt = (
            f"{render_template(step.prompt, collected)}\n\n"
            f"[지금까지 대화에서 파악된 내담자 정보]\n{ctx}\n\n"
            f"위 지시와 정보를 바탕으로, 캐릭터 톤을 유지한 짧은 메시지만 출력하세요. "
            f"설명·메타발화·따옴표 없이 대사만."
        )
        try:
            text = await self._llm.generate(
                user_prompt,
                cacheable_system=cacheable_system,
                volatile_system=volatile_system,
            )
            text = (text or "").strip()
            return text or fallback
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "dynamic_step_llm_failed",
                layer="WORKFLOW",
                step_id=step.id,
                error=str(e),
            )
            return fallback

    async def _execute_action_step(
        self,
        step: WorkflowStep,
        session: WorkflowSession,
        profile_endpoint: str | None = None,
        profile_headers: dict | None = None,
    ) -> StepResult:
        """action step을 실행한다.

        1. endpoint: step.endpoint > profile_endpoint (둘 다 없으면 에러)
        2. headers: step.headers_template + profile_headers 병합
        3. payload: step.payload_template
        4. 호출 성공 -> on_success_message + 다음 스텝 진행
        5. 호출 실패 -> on_error_message + 워크플로우 종료
        """
        if not self._action_client:
            logger.error(
                "action_step_no_client",
                layer="WORKFLOW",
                step_id=step.id,
            )
            return StepResult(
                bot_message=step.on_error_message or "외부 연동 기능이 비활성화되어 있습니다.",
                step_id=step.id,
                step_type="action",
                collected=dict(session.collected),
                completed=True,
            )

        # 엔드포인트 결정: step > profile
        endpoint = step.endpoint or profile_endpoint
        if not endpoint:
            logger.error(
                "action_step_no_endpoint",
                layer="WORKFLOW",
                step_id=step.id,
            )
            return StepResult(
                bot_message=step.on_error_message or "외부 API 엔드포인트가 설정되지 않았습니다.",
                step_id=step.id,
                step_type="action",
                collected=dict(session.collected),
                completed=True,
            )

        # 헤더 병합: profile 기본값 + step 오버라이드
        merged_headers = dict(profile_headers or {})
        if step.headers_template:
            merged_headers.update(step.headers_template)

        try:
            response_data = await self._action_client.call(
                endpoint=endpoint,
                method=step.http_method,
                headers=merged_headers if merged_headers else None,
                payload=step.payload_template if step.payload_template else None,
                timeout=step.timeout_seconds,
                collected=session.collected,
            )

            # 응답 데이터를 세션에 저장 (save_as가 있으면)
            if step.save_as:
                session.collected[step.save_as] = response_data

            # 콜백 응답도 세션에 기록
            session.callback_response = response_data

            success_message = render_template(
                step.on_success_message or "처리가 완료되었습니다.",
                session.collected,
            )

            logger.info(
                "action_step_success",
                layer="WORKFLOW",
                step_id=step.id,
                endpoint=endpoint[:100],
            )

            return StepResult(
                bot_message=success_message,
                step_id=step.id,
                step_type="action",
                collected=dict(session.collected),
                action_result=response_data,
            )

        except WorkflowActionError as e:
            logger.warning(
                "action_step_failed",
                layer="WORKFLOW",
                step_id=step.id,
                endpoint=endpoint[:100],
                status_code=e.status_code,
                error=str(e),
            )

            error_message = render_template(
                step.on_error_message or "외부 시스템 연동 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                session.collected,
            )

            return StepResult(
                bot_message=error_message,
                step_id=step.id,
                step_type="action",
                collected=dict(session.collected),
                completed=True,
                action_result={"error": str(e), "status_code": e.status_code},
            )


def _resolve_next(step: WorkflowStep, user_input: str) -> str | None:
    """사용자 입력에 따라 다음 스텝 ID를 결정한다."""
    if step.branches:
        # 정확한 매칭 시도
        if user_input in step.branches:
            return step.branches[user_input]
        # 대소문자 무시 매칭
        input_lower = user_input.strip().lower()
        for key, next_id in step.branches.items():
            if key.lower() == input_lower:
                return next_id
        # 번호 매칭 (1, 2, 3...)
        if user_input.strip().isdigit():
            idx = int(user_input.strip()) - 1
            keys = list(step.branches.keys())
            if 0 <= idx < len(keys):
                return step.branches[keys[idx]]
        # 부분문자열 매칭은 제거(오매칭·맥락 무시 원인) — 자유입력은 advance()에서
        # 공통 SemanticClassifier가 의미로 분류한다. 여기선 fallback next만 반환.
        return step.next
    return step.next


def _validate_input(step: WorkflowStep, user_input: str) -> str:
    """입력 검증. 실패 시 에러 메시지, 성공 시 빈 문자열."""
    if not step.validation:
        return ""

    # select 타입: options 중 하나여야 함
    if step.type == "select" and step.options:
        input_lower = user_input.strip().lower()
        # 정확 매칭
        if any(opt.lower() == input_lower for opt in step.options):
            return ""
        # 번호 매칭
        if user_input.strip().isdigit():
            idx = int(user_input.strip()) - 1
            if 0 <= idx < len(step.options):
                return ""
        options_str = ", ".join(f"{i+1}. {opt}" for i, opt in enumerate(step.options))
        return f"다음 중 하나를 선택해주세요:\n{options_str}"

    # 패턴 검증
    pattern = _VALIDATORS.get(step.validation)
    if pattern and not pattern.match(user_input.strip()):
        hints = {
            "phone": "전화번호 형식이 올바르지 않습니다. (예: 010-1234-5678)",
            "email": "이메일 형식이 올바르지 않습니다. (예: user@example.com)",
            "number": "숫자만 입력해주세요.",
            "date": "날짜 형식이 올바르지 않습니다. (예: 2026-03-13)",
        }
        return hints.get(step.validation, f"입력 형식이 올바르지 않습니다. ({step.validation})")

    return ""
