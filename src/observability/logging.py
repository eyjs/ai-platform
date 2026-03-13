"""Structured Logging: JSON 구조화 로그 + request_id 전파 + Layer Context.

모든 레이어(Gateway, Router, Agent, Tool, Safety)에서
request_id를 기반으로 한 추적 가능한 구조화 로그를 출력한다.

Layer-Aware 로깅:
    logger = get_logger(__name__)
    logger.info("event", latency_ms=45.2)                    # 기본 (layer 없음)
    logger.info("event", layer="ROUTER", component="L0")     # layer/component 명시
    logger.error("fail", layer="AGENT", error_code="ERR_AGENT_004")

    # AppError 예외 로깅 시 layer/error_code 자동 추출:
    except AppError as e:
        logger.error("fail", exc_info=True)
        # → {"layer": "ROUTER", "error_code": "ERR_ROUTER_001", ...} 자동 포함
"""

import json
import logging
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RequestContext:
    """요청 단위 컨텍스트. contextvars로 async 전파."""

    request_id: str = ""
    session_id: str = ""
    profile_id: str = ""
    user_id: str = ""
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed_ms(self) -> float:
        return (time.time() - self.start_time) * 1000


# 전역 ContextVar — async 경계를 넘어 자동 전파
request_context: ContextVar[RequestContext] = ContextVar(
    "request_context", default=RequestContext(),
)


def _extract_app_error_fields(exc_info) -> dict[str, str]:
    """exc_info에서 AppError 필드를 추출한다. AppError가 아니면 빈 dict."""
    if not exc_info or not exc_info[1]:
        return {}
    exc = exc_info[1]
    # 순환 import 방지: duck typing으로 체크
    if hasattr(exc, "layer") and hasattr(exc, "error_code"):
        result = {"layer": exc.layer, "error_code": exc.error_code}
        if hasattr(exc, "component") and exc.component:
            result["component"] = exc.component
        return result
    return {}


class StructuredFormatter(logging.Formatter):
    """JSON 구조화 로그 포맷터.

    출력 예:
    {"ts":"2026-03-13T10:00:00","level":"ERROR","logger":"src.router.ai_router",
     "msg":"L0_fallback","request_id":"abc-123","layer":"ROUTER",
     "component":"ContextResolver","error_code":"ERR_ROUTER_001"}
    """

    def format(self, record: logging.LogRecord) -> str:
        ctx = request_context.get()

        entry: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # request context
        if ctx.request_id:
            entry["request_id"] = ctx.request_id
        if ctx.session_id:
            entry["session_id"] = ctx.session_id
        if ctx.profile_id:
            entry["profile_id"] = ctx.profile_id

        # 추가 필드 (logger.info("msg", layer="ROUTER", component="L0", ...))
        extra_data = getattr(record, "_structured_data", None)
        if extra_data and isinstance(extra_data, dict):
            entry.update(extra_data)

        # AppError 예외의 layer/error_code 자동 추출
        if record.exc_info:
            app_fields = _extract_app_error_fields(record.exc_info)
            for k, v in app_fields.items():
                entry.setdefault(k, v)

        # 에러 정보
        if record.exc_info and record.exc_info[1]:
            entry["error"] = str(record.exc_info[1])
            entry["error_type"] = type(record.exc_info[1]).__name__

        return json.dumps(entry, ensure_ascii=False, default=str)


class HumanReadableFormatter(logging.Formatter):
    """개발 모드용 읽기 쉬운 포맷.

    출력 예 (layer 있을 때):
    10:00:00 ERROR [abc-123] [ROUTER:ContextResolver] L0_fallback error=LLM JSON 파싱 실패

    출력 예 (layer 없을 때):
    10:00:00 INFO  [abc-123] router.ai_router: route_complete question_type=STANDALONE
    """

    def format(self, record: logging.LogRecord) -> str:
        ctx = request_context.get()
        ts = self.formatTime(record, "%H:%M:%S")
        level = record.levelname.ljust(5)
        rid = f"[{ctx.request_id[:8]}]" if ctx.request_id else "[--------]"

        # 추가 필드
        extra_data = getattr(record, "_structured_data", None) or {}

        # AppError 예외에서 layer/error_code 자동 추출
        if record.exc_info:
            app_fields = _extract_app_error_fields(record.exc_info)
            for k, v in app_fields.items():
                extra_data.setdefault(k, v)

        layer = extra_data.pop("layer", "")
        component = extra_data.pop("component", "")

        # layer가 있으면 [LAYER:Component] 형태, 없으면 logger name
        if layer:
            tag = f"[{layer}:{component}]" if component else f"[{layer}]"
        else:
            name = record.name
            if name.startswith("src."):
                name = name[4:]
            tag = f"{name}:"

        msg = record.getMessage()

        extra_str = ""
        if extra_data:
            parts = [f"{k}={v}" for k, v in extra_data.items()]
            extra_str = " " + " ".join(parts)

        line = f"{ts} {level} {rid} {tag} {msg}{extra_str}"

        if record.exc_info and record.exc_info[1]:
            line += f" ERROR={record.exc_info[1]}"

        return line


class StructuredLogger:
    """구조화 로거 래퍼.

    추가 키워드 인자를 구조화 데이터로 자동 첨부한다.

    사용법:
        logger.info("event_name", latency_ms=45.2, chunks=5)
        logger.info("event_name", layer="ROUTER", component="L0", latency_ms=45.2)
        logger.error("fail", layer="AGENT", error_code="ERR_AGENT_004", exc_info=True)
    """

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def _log(self, level: int, msg: str, kwargs: dict) -> None:
        if not self._logger.isEnabledFor(level):
            return
        exc_info = kwargs.pop("exc_info", None)
        if exc_info is True:
            exc_info = sys.exc_info()
        record = self._logger.makeRecord(
            name=self._logger.name,
            level=level,
            fn="",
            lno=0,
            msg=msg,
            args=(),
            exc_info=exc_info,
        )
        record._structured_data = kwargs  # type: ignore[attr-defined]
        self._logger.handle(record)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, kwargs)

    def critical(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, msg, kwargs)


def get_logger(name: str) -> StructuredLogger:
    """구조화 로거 인스턴스 생성."""
    return StructuredLogger(name)


def configure_logging(
    level: str = "INFO",
    json_format: bool = True,
) -> None:
    """글로벌 로깅 설정.

    Args:
        level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR)
        json_format: True면 JSON, False면 사람이 읽기 쉬운 포맷
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 기존 핸들러 제거
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper(), logging.INFO))

    if json_format:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(HumanReadableFormatter())

    root.addHandler(handler)

    # 서드파티 로거 레벨 조정 (너무 시끄러운 라이브러리 억제)
    for noisy in ("httpx", "httpcore", "asyncpg", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
