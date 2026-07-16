"""우리가 의존하는 것들의 생존 감시 — 내부 링크(KMS·DocForge) + LLM 서빙.

KMS ↔ ai-platform ↔ DocForge 는 공유 도커 네트워크(kms-aip-shared)로 항상
연결되어 있어야 한다(운영 원칙). 네트워크는 컴포즈가 보장하지만 "연결이
살아있는지"는 아무도 보지 않아, 상대 서비스가 죽으면 잡 실패·빈손 검색으로만
간접 발현됐다(실사고: docforge 포화 중 kms_sync 잡이 빈 에러로 무음 소진).

LLM 서빙(DGX primary + 로컬 MLX 폴백)도 같은 이유로 여기서 본다. 폴백은 평소
불리지 않으니 조용히 썩는다 — 실사고: 8104(리포트 14B)가 7일간 /health 200을
주면서 생성은 전건 500이었고(HF 캐시 미완성 blob → 토크나이저 깨짐), 아무도
몰랐다. **그래서 LLM은 GET이 아니라 실제 1토큰 생성으로 찌른다**(GenerateProbe).
GET으로 봤다면 그 7일을 똑같이 놓쳤다.

이 모니터는:
- 부팅 시 1회 + 주기(기본 60s)로 각 대상을 찌른다
- 상태 전이(up→down, down→up)만 WARN/INFO 로 로깅한다 (무전이 무소음)
- 최신 상태를 /api/health 응답에 노출한다 (외부에서 상시 확인 가능)

실패해도 요청 경로에 개입하지 않는다 — 순수 관측(장애 시 fail-soft).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from src.observability.logging import get_logger

logger = get_logger(__name__)

_PROBE_TIMEOUT = 5.0
# 생성 프로브는 GET보다 넉넉히 — 1토큰이라도 프롬프트 처리와 큐 대기가 있다.
_GENERATE_TIMEOUT = 30.0


@dataclass(frozen=True)
class GenerateProbe:
    """LLM 서빙 대상 — 실제 1토큰 생성으로 '진짜 서빙 가능한가'를 본다.

    url: OpenAI 호환 베이스(예: http://host:8104, http://dgx:11434). /v1/chat/completions를 친다.
    model: DGX(ollama)는 모델명을 구분하므로 필수. MLX 서버는 한 모델만 서빙해 무시된다.
    """

    url: str
    model: str = "probe"
    timeout: float = _GENERATE_TIMEOUT


class LinkMonitor:
    """의존 대상 생존 감시자.

    targets: 이름 → 대상.
      - str  : 헬스 엔드포인트 URL을 GET (내부 링크용)
      - GenerateProbe : 실제 1토큰 생성으로 확인 (LLM 서빙용)

    예: {"kms": "http://kms-api:3000/api/health/live",
         "llm:dgx": GenerateProbe("http://dgx:11434", model="qwen3.6:35b-a3b")}
    """

    def __init__(
        self,
        targets: dict[str, str | GenerateProbe],
        interval_seconds: int = 60,
    ):
        self._targets = {name: t for name, t in targets.items() if _target_url(t)}
        self._interval = interval_seconds
        self._status: dict[str, dict] = {
            name: {"up": None, "checked_at": None, "detail": "unchecked"}
            for name in self._targets
        }
        self._task: asyncio.Task | None = None

    @property
    def status(self) -> dict[str, dict]:
        """링크별 최신 상태 스냅샷 (health 응답용)."""
        return {name: dict(s) for name, s in self._status.items()}

    async def check_once(self) -> dict[str, dict]:
        """모든 대상을 1회 점검하고 상태 전이를 로깅한다."""
        # 타임아웃은 프로브별로 지정하므로 클라이언트 기본값은 두지 않는다.
        async with httpx.AsyncClient(timeout=None) as client:
            results = await asyncio.gather(
                *(self._probe(client, name, t) for name, t in self._targets.items()),
            )
        for name, up, detail in results:
            prev = self._status[name]["up"]
            self._status[name] = {
                "up": up,
                "checked_at": round(time.time(), 1),
                "detail": detail,
            }
            if prev is None:
                # 부팅 첫 점검: 상태를 항상 남긴다 (연결 전제의 명시적 증거)
                log = logger.info if up else logger.warning
                log("link_status", link=name, up=up, detail=detail)
            elif prev != up:
                if up:
                    logger.info("link_recovered", link=name, detail=detail)
                else:
                    logger.warning("link_down", link=name, detail=detail)
        return self.status

    @staticmethod
    async def _probe(
        client: httpx.AsyncClient, name: str, target: str | GenerateProbe,
    ) -> tuple[str, bool, str]:
        try:
            if isinstance(target, GenerateProbe):
                return await LinkMonitor._probe_generate(client, name, target)
            resp = await client.get(target, timeout=_PROBE_TIMEOUT)
            if resp.status_code == 200:
                return name, True, "ok"
            return name, False, f"http {resp.status_code}"
        except Exception as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            return name, False, detail

    @staticmethod
    async def _probe_generate(
        client: httpx.AsyncClient, name: str, probe: GenerateProbe,
    ) -> tuple[str, bool, str]:
        """1토큰 생성으로 서빙 가능 여부를 본다.

        200이어도 choices 구조가 없으면 down으로 본다 — 200과 '생성 가능'은 다르다는 것이
        이 프로브의 존재 이유다. 반대로 빈 content는 up으로 둔다: thinking 모델은
        1토큰 상한에서 content가 비어 나올 수 있는데, 그건 고장이 아니다.
        """
        resp = await client.post(
            f"{probe.url.rstrip('/')}/v1/chat/completions",
            json={
                "model": probe.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            },
            timeout=probe.timeout,
        )
        if resp.status_code != 200:
            # 본문 앞부분까지 남긴다 — 8104의 "Generation failed: unicode-escape"처럼
            # 원인이 본문에만 있는 경우가 있다.
            return name, False, f"http {resp.status_code}: {resp.text[:120]}"
        try:
            choices = resp.json()["choices"]
            if not choices or "message" not in choices[0]:
                raise KeyError("choices[0].message")
        except Exception as e:
            return name, False, f"200이나 응답 형식 불량: {type(e).__name__}: {e}"
        return name, True, "generate ok"

    async def start(self) -> None:
        """부팅 1회 점검 후 주기 감시 태스크를 시작한다. interval<=0 이면 1회만."""
        await self.check_once()
        if self._interval > 0 and self._targets:
            self._task = asyncio.create_task(self._loop())
            logger.info(
                "link_monitor_started",
                links=sorted(self._targets),
                interval_seconds=self._interval,
            )

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.check_once()
            except Exception as e:  # 감시 실패가 감시를 죽이면 안 됨
                logger.warning("link_monitor_check_error", error=repr(e))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


def _target_url(target: str | GenerateProbe) -> str:
    return target.url if isinstance(target, GenerateProbe) else target


def build_llm_probe_targets(settings) -> dict[str, GenerateProbe]:
    """LLM 서빙 감시 대상 — 실제로 배선된 것만.

    배선되지 않은 서버를 감시하면 쓰지도 않는 것의 장애로 시끄러워지고, 감시 목록이
    실제 배선과 어긋나 "죽은 설정"이 된다. 그래서 배선 규칙(ProviderFactory)을 그대로 따른다:
    - DGX가 설정되면 primary로 감시한다.
    - 로컬 MLX는 폴백으로 실제 쓰일 때만 감시한다 — 즉 DGX가 없거나(로컬이 primary),
      dgx_local_fallback이 켜져 있을 때.
    같은 URL을 여러 역할이 공유하면(main·fortune=8106) 한 번만 찌른다.
    """
    dgx_url = getattr(settings, "dgx_llm_url", "") or ""
    targets: dict[str, GenerateProbe] = {}
    if dgx_url:
        targets["llm:dgx"] = GenerateProbe(
            url=dgx_url,
            model=getattr(settings, "dgx_main_model", "") or "probe",
        )

    local_wired = not dgx_url or getattr(settings, "dgx_local_fallback", False)
    if not local_wired:
        return targets

    seen: set[str] = set()
    for role, attr in (
        ("main", "main_llm_server_url"),
        ("router", "router_llm_server_url"),
        ("report", "report_llm_server_url"),
        ("fortune", "fortune_llm_server_url"),
        ("orchestrator", "orchestrator_server_url"),
    ):
        url = (getattr(settings, attr, "") or "").rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        targets[f"llm:local:{role}"] = GenerateProbe(url=url)
    return targets


def build_link_targets(settings) -> dict[str, str]:
    """settings 의 내부 서비스 URL 을 헬스 엔드포인트로 변환한다.

    미설정(빈 URL) 링크는 감시 대상에서 제외된다 — KMS 없이도 기동해야
    하는 graceful degradation 원칙과 정합.
    """
    targets: dict[str, str] = {}
    kms = (getattr(settings, "kms_api_url", "") or "").rstrip("/")
    if kms:
        targets["kms"] = f"{kms}/health/live"
    docforge = (getattr(settings, "docforge_url", "") or "").rstrip("/")
    if docforge:
        targets["docforge"] = f"{docforge}/v1/health"
    return targets
