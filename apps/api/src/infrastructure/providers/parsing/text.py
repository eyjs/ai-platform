"""텍스트 기반 파서 (폴백).

외부 API 없이 순수 텍스트 추출. PDF는 pdfplumber로 텍스트만 뽑고,
Markdown/CSV는 그대로 반환한다.
"""

from typing import List

from src.infrastructure.providers.base import ParsingProvider
from src.observability.logging import get_logger

logger = get_logger(__name__)

_SUPPORTED = ["application/pdf", "text/markdown", "text/csv", "text/plain"]


class TextParsingProvider(ParsingProvider):
    """순수 텍스트 추출 파서. 표 구조는 보존하지 않는다."""

    async def parse(self, file_bytes: bytes, mime_type: str) -> str:
        if mime_type in ("text/markdown", "text/csv", "text/plain"):
            return file_bytes.decode("utf-8", errors="replace")

        if mime_type == "application/pdf":
            return self._extract_pdf_text(file_bytes)

        raise ValueError(f"Unsupported mime_type: {mime_type}")

    def supported_types(self) -> List[str]:
        return list(_SUPPORTED)

    @staticmethod
    def _extract_pdf_text(file_bytes: bytes) -> str:
        try:
            import pdfplumber
        except ImportError:
            raise ImportError("pdfplumber is required for PDF text extraction: pip install pdfplumber")

        import io

        pages = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n\n".join(pages)
