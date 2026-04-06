"""파싱 품질 메트릭 수집.

마크다운 출력에서 구조적 지표를 추출하여 ParseMetrics를 생성한다.
파서 간 비교, 품질 추적, 이상 감지에 사용.
"""

from __future__ import annotations

import re

from src.pipeline.parsing.base import DocumentProfile, ParseMetrics


def compute_markdown_metrics(
    markdown: str,
    parse_time_ms: float = 0.0,
    total_pages: int = 0,
    parser_used: str = "",
    parser_reason: str = "",
    document_profile: DocumentProfile | None = None,
) -> ParseMetrics:
    """마크다운 텍스트에서 구조적 메트릭을 계산한다."""
    if not markdown:
        return ParseMetrics(
            parse_time_ms=parse_time_ms,
            total_pages=total_pages,
            parser_used=parser_used,
            parser_reason=parser_reason,
            document_profile=document_profile,
        )

    lines = markdown.split("\n")
    total_lines = len(lines)
    non_empty_lines = [l for l in lines if l.strip()]

    # 헤딩 카운트
    heading_count = sum(1 for l in lines if re.match(r"^#{1,6}\s", l))

    # 테이블 카운트 (| --- | 구분선 패턴으로 감지)
    table_count = sum(1 for l in lines if _is_table_separator(l.strip()))

    # 리스트 카운트
    list_count = sum(1 for l in lines if re.match(r"^\s*[-*+]\s", l) or re.match(r"^\s*\d+\.\s", l))

    # 코드 블록 카운트
    code_block_count = sum(1 for l in lines if l.strip().startswith("```"))
    code_block_count = code_block_count // 2  # 열림/닫힘 쌍

    # 이미지 참조
    image_ref_count = sum(1 for l in lines if re.search(r"!\[.*?\]\(.*?\)", l))

    # 품질 지표
    empty_line_ratio = (total_lines - len(non_empty_lines)) / total_lines if total_lines > 0 else 0.0
    avg_line_length = (
        sum(len(l) for l in non_empty_lines) / len(non_empty_lines)
        if non_empty_lines
        else 0.0
    )

    return ParseMetrics(
        parse_time_ms=parse_time_ms,
        total_chars=len(markdown),
        total_pages=total_pages,
        heading_count=heading_count,
        table_count=table_count,
        list_count=list_count,
        code_block_count=code_block_count,
        image_ref_count=image_ref_count,
        empty_line_ratio=round(empty_line_ratio, 3),
        avg_line_length=round(avg_line_length, 1),
        parser_used=parser_used,
        parser_reason=parser_reason,
        document_profile=document_profile,
    )


def _is_table_separator(line: str) -> bool:
    """마크다운 테이블 구분선인지 판별한다. (| --- | --- |)"""
    if not line.startswith("|") or not line.endswith("|"):
        return False
    inner = line[1:-1]
    cells = inner.split("|")
    return all(c.strip().replace("-", "").replace(":", "") == "" for c in cells)
