"""Layer-Aware 예외 계층.

에러를 두 가지로 구분한다:
1. AI 결함 (AIError 계열) — LLM 파싱 실패, 의도 분류 오류 등. Fallback 가능.
2. 시스템 결함 (InfraError 계열) — DB, 네트워크, 설정 등. 즉시 상위로 전파.

사용법:
    from src.common.exceptions import AIError, InfraError, RouterAIError

    # AI 결함 → 잡아서 Fallback
    raise RouterAIError("LLM이 JSON 형식을 깨뜨림", error_code="ERR_ROUTER_001")

    # 시스템 결함 → 절대 잡지 말고 위로 던져야 함
    raise InfraError("DB 커넥션 풀 고갈", error_code="ERR_INFRA_001")

레이어 상수:
    GATEWAY, ROUTER, AGENT, TOOL, SAFETY, INFRA, PIPELINE
"""


# --- Layer 상수 ---

GATEWAY = "GATEWAY"
ROUTER = "ROUTER"
AGENT = "AGENT"
TOOL = "TOOL"
SAFETY = "SAFETY"
INFRA = "INFRA"
PIPELINE = "PIPELINE"


# --- 기본 예외 ---


class AppError(Exception):
    """모든 커스텀 예외의 기반 클래스.

    Attributes:
        layer: 에러가 발생한 레이어 (GATEWAY, ROUTER, AGENT, TOOL, SAFETY, INFRA, PIPELINE)
        error_code: 표준화된 에러 코드 (ERR_{LAYER}_{번호})
        component: 에러가 발생한 모듈/클래스명
        details: 추가 디버깅 정보
    """

    def __init__(
        self,
        message: str,
        *,
        layer: str,
        error_code: str,
        component: str = "",
        details: dict | None = None,
    ):
        super().__init__(message)
        self.layer = layer
        self.error_code = error_code
        self.component = component
        self.details = details or {}


# --- AI 결함 (Fallback 가능) ---


class AIError(AppError):
    """AI/LLM 관련 에러. 잡아서 Fallback 처리해야 한다.

    예: LLM JSON 파싱 실패, 의도 분류 이상, 리랭킹 스코어 이상 등.
    """

    pass


class RouterAIError(AIError):
    """Router Layer AI 에러."""

    def __init__(self, message: str, *, error_code: str = "ERR_ROUTER_001", component: str = "", details: dict | None = None):
        super().__init__(message, layer=ROUTER, error_code=error_code, component=component, details=details)


class AgentAIError(AIError):
    """Agent Layer AI 에러."""

    def __init__(self, message: str, *, error_code: str = "ERR_AGENT_001", component: str = "", details: dict | None = None):
        super().__init__(message, layer=AGENT, error_code=error_code, component=component, details=details)


class ToolAIError(AIError):
    """Tool Layer AI 에러."""

    def __init__(self, message: str, *, error_code: str = "ERR_TOOL_001", component: str = "", details: dict | None = None):
        super().__init__(message, layer=TOOL, error_code=error_code, component=component, details=details)


class SafetyAIError(AIError):
    """Safety Layer AI 에러."""

    def __init__(self, message: str, *, error_code: str = "ERR_SAFETY_001", component: str = "", details: dict | None = None):
        super().__init__(message, layer=SAFETY, error_code=error_code, component=component, details=details)


# --- 시스템 결함 (위로 전파, Fallback 금지) ---


class InfraError(AppError):
    """인프라/시스템 에러. 절대 Fallback으로 덮으면 안 된다.

    예: DB 커넥션 풀 고갈, 네트워크 타임아웃, 설정 누락 등.
    """

    def __init__(self, message: str, *, error_code: str = "ERR_INFRA_001", component: str = "", details: dict | None = None):
        super().__init__(message, layer=INFRA, error_code=error_code, component=component, details=details)


class GatewayError(AppError):
    """Gateway Layer 시스템 에러."""

    def __init__(self, message: str, *, error_code: str = "ERR_GATEWAY_001", component: str = "", details: dict | None = None):
        super().__init__(message, layer=GATEWAY, error_code=error_code, component=component, details=details)


class PipelineError(AppError):
    """Pipeline Layer 시스템 에러."""

    def __init__(self, message: str, *, error_code: str = "ERR_PIPELINE_001", component: str = "", details: dict | None = None):
        super().__init__(message, layer=PIPELINE, error_code=error_code, component=component, details=details)
