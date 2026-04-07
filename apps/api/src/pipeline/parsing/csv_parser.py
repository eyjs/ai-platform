"""CSV 파서.

CSV → Markdown 테이블 변환.
대용량 파일은 시트 단위로 분할하여 마크다운 섹션으로 구성한다.
"""

from __future__ import annotations

import csv
import io
import time

from src.observability.logging import get_logger
from src.pipeline.parsing.base import FormatParser, ParseMetrics, ParseResult
from src.pipeline.parsing.metrics import compute_markdown_metrics

logger = get_logger(__name__)

_SUPPORTED = ["text/csv"]

# CSV 파일 최대 행 수 (메모리 보호)
_MAX_ROWS = 10000
_PREVIEW_ROWS = 100  # 미리보기 행 수 (초과 시 요약 추가)


class CsvParser:
    """CSV → Markdown 테이블 변환."""

    def __init__(self, max_rows: int = _MAX_ROWS):
        self._max_rows = max_rows

    async def parse(self, file_bytes: bytes, file_name: str = "") -> ParseResult:
        t0 = time.time()

        text = file_bytes.decode("utf-8", errors="replace")

        # 구분자 자동 감지
        dialect = _detect_dialect(text)
        reader = csv.reader(io.StringIO(text), dialect=dialect)

        # 헤더 1행 + 데이터 max_rows행까지 읽기
        rows: list[list[str]] = []
        truncated = False
        for i, row in enumerate(reader):
            if i > self._max_rows:  # 헤더(0) + 데이터(1~max_rows)
                truncated = True
                break
            rows.append(row)

        if not rows:
            return ParseResult(
                markdown="",
                metrics=ParseMetrics(parser_used="csv", parser_reason="빈 CSV"),
                source_mime_type="text/csv",
                source_file_name=file_name,
            )

        markdown = _rows_to_markdown_table(rows, file_name)
        elapsed_ms = (time.time() - t0) * 1000

        data_rows = len(rows) - 1  # 헤더 제외

        if truncated:
            markdown += f"\n\n> CSV 데이터가 {self._max_rows}행에서 잘렸습니다. 원본은 더 많은 데이터를 포함합니다.\n"

        metrics = compute_markdown_metrics(
            markdown=markdown,
            parse_time_ms=elapsed_ms,
            total_pages=1,
            parser_used="csv",
            parser_reason=f"data_rows={data_rows}, truncated={truncated}",
        )

        logger.info(
            "csv_parsed",
            file_name=file_name,
            rows=data_rows,
            cols=len(rows[0]) if rows else 0,
            truncated=truncated,
            latency_ms=round(elapsed_ms, 1),
        )

        return ParseResult(
            markdown=markdown,
            metrics=metrics,
            source_mime_type="text/csv",
            source_file_name=file_name,
        )

    def supported_mime_types(self) -> list[str]:
        return list(_SUPPORTED)


def _detect_dialect(text: str) -> csv.Dialect:
    """CSV 구분자를 자동 감지한다."""
    try:
        sample = text[:8192]
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _rows_to_markdown_table(rows: list[list[str]], file_name: str = "") -> str:
    """행 데이터를 마크다운 테이블로 변환한다."""
    if not rows:
        return ""

    # 첫 행을 헤더로 사용
    header = rows[0]
    data_rows = rows[1:]

    # 열 너비 정규화 (모든 행의 열 수를 헤더에 맞춤)
    col_count = len(header)
    normalized = []
    for row in data_rows:
        if len(row) < col_count:
            row = row + [""] * (col_count - len(row))
        elif len(row) > col_count:
            row = row[:col_count]
        normalized.append(row)

    # 파일명 헤더
    parts: list[str] = []
    if file_name:
        parts.append(f"## {file_name}\n")

    # 헤더 행
    header_cells = [_escape_pipe(h) for h in header]
    parts.append("| " + " | ".join(header_cells) + " |")

    # 구분선
    parts.append("| " + " | ".join(["---"] * col_count) + " |")

    # 데이터 행
    for row in normalized:
        cells = [_escape_pipe(c) for c in row]
        parts.append("| " + " | ".join(cells) + " |")

    return "\n".join(parts)


def _escape_pipe(value: str) -> str:
    """마크다운 테이블 셀에서 파이프 문자를 이스케이프한다."""
    return value.replace("|", "\\|").replace("\n", " ").strip()
