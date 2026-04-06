"""ParsingProvider 어댑터.

새 ParsingEngine을 기존 ParsingProvider 인터페이스에 연결한다.
IngestPipeline은 ParsingProvider.parse(file_bytes, mime_type)를 호출하므로
이 어댑터가 file_name을 복원하여 ParsingEngine에 전달한다.
"""

from __future__ import annotations

from src.infrastructure.providers.base import ParsingProvider
from src.pipeline.parsing.engine import ALLOWED_EXTENSIONS, ParsingEngine


# MIME → 대표 확장자
_MIME_TO_EXT: dict[str, str] = {
    "application/pdf": "document.pdf",
    "text/csv": "document.csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "document.xlsx",
    "application/vnd.ms-excel": "document.xls",
}


class ParsingEngineProvider(ParsingProvider):
    """ParsingEngine을 ParsingProvider 인터페이스로 래핑한다.

    기존 IngestPipeline, ProviderFactory와 호환.
    """

    def __init__(self, engine: ParsingEngine):
        self._engine = engine

    async def parse(self, file_bytes: bytes, mime_type: str) -> str:
        """파일을 파싱하여 마크다운 문자열을 반환한다."""
        file_name = _MIME_TO_EXT.get(mime_type)
        if file_name is None:
            supported = ", ".join(ALLOWED_EXTENSIONS)
            raise ValueError(
                f"지원하지 않는 파일 형식입니다: {mime_type} — "
                f"허용 확장자: {supported}"
            )

        result = await self._engine.parse(file_bytes, file_name)
        return result.markdown

    def supported_types(self) -> list[str]:
        return list(_MIME_TO_EXT.keys())
