"""LlamaParse Vision Parser.

PDF/이미지를 마크다운으로 변환. 표 구조를 완벽하게 보존한다.
SDK 의존 없이 REST API 직접 호출. 무료 1000페이지/일.
"""

import asyncio
from typing import List

import httpx

from src.infrastructure.providers.base import ParsingProvider
from src.observability.logging import get_logger

logger = get_logger(__name__)

_SUPPORTED = ["application/pdf", "image/png", "image/jpeg"]

# LlamaParse REST API (SDK 없이 직접 호출 — 의존성 최소화)
_LLAMA_PARSE_URL = "https://api.cloud.llamaindex.ai/api/parsing/upload"
_LLAMA_PARSE_STATUS_URL = "https://api.cloud.llamaindex.ai/api/parsing/job/{job_id}"
_LLAMA_PARSE_RESULT_URL = "https://api.cloud.llamaindex.ai/api/parsing/job/{job_id}/result/markdown"


class LlamaParseProvider(ParsingProvider):
    """LlamaParse API 기반 Vision Parser.

    PDF 표/레이아웃을 마크다운으로 변환한다.
    SDK 의존 없이 REST API 직접 호출.
    """

    def __init__(self, api_key: str, timeout: float = 120.0):
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    async def parse(self, file_bytes: bytes, mime_type: str) -> str:
        if mime_type not in _SUPPORTED:
            raise ValueError(f"LlamaParse unsupported mime_type: {mime_type}")

        ext = _mime_to_ext(mime_type)
        headers = {"Authorization": f"Bearer {self._api_key}"}

        # 1. 파일 업로드 → job_id
        files = {"file": (f"document{ext}", file_bytes, mime_type)}
        data = {"result_type": "markdown"}

        resp = await self._client.post(_LLAMA_PARSE_URL, headers=headers, files=files, data=data)
        resp.raise_for_status()
        job_id = resp.json()["id"]
        logger.info("llamaparse_job_created", job_id=job_id)

        # 2. 폴링 — 완료 대기
        status_url = _LLAMA_PARSE_STATUS_URL.format(job_id=job_id)
        for _ in range(60):  # 최대 60회 (2초 간격 = 2분)
            await asyncio.sleep(2)
            status_resp = await self._client.get(status_url, headers=headers)
            status_resp.raise_for_status()
            status = status_resp.json()["status"]

            if status == "SUCCESS":
                break
            if status == "ERROR":
                error_msg = status_resp.json().get("error", "Unknown error")
                raise RuntimeError(f"LlamaParse failed: {error_msg}")
        else:
            raise TimeoutError(f"LlamaParse job {job_id} timed out")

        # 3. 마크다운 결과 가져오기
        result_url = _LLAMA_PARSE_RESULT_URL.format(job_id=job_id)
        result_resp = await self._client.get(result_url, headers=headers)
        result_resp.raise_for_status()
        markdown = result_resp.json()["markdown"]

        logger.info("llamaparse_complete", job_id=job_id, chars=len(markdown))
        return markdown

    async def close(self) -> None:
        """HTTP 클라이언트 정리."""
        await self._client.aclose()

    def supported_types(self) -> List[str]:
        return list(_SUPPORTED)


def _mime_to_ext(mime_type: str) -> str:
    return {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
    }[mime_type]
