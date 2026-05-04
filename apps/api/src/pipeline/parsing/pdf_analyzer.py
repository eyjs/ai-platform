"""PDF 문서 사전 분석기.

PyMuPDF로 PDF 내부를 빠르게 스캔하여 DocumentProfile을 생성한다.
텍스트/이미지 비율, 표 감지, 복잡도 등급을 판단하여
최적의 파서를 자동 라우팅하는 근거를 제공한다.

성능: CPU only, 페이지당 ~1ms. GPU 불필요.
"""

from __future__ import annotations

from src.observability.logging import get_logger
from src.pipeline.parsing.base import DocumentComplexity, DocumentProfile

logger = get_logger(__name__)

# 라우팅 임계값
_MIN_CHARS_PER_PAGE = 50        # 이 이하면 이미지 기반 페이지로 판정
_IMAGE_HEAVY_RATIO = 0.5        # 이미지 전용 페이지가 전체의 50% 이상이면 IMAGE_HEAVY
_IMAGE_AREA_TABLE_HINT = 0.15   # 이미지 면적 비율이 이 이상이면 표/차트 가능성
_TABLE_LINE_KEYWORDS = {"─", "━", "│", "┃", "|", "+", "-+-"}


def analyze_pdf(file_bytes: bytes) -> DocumentProfile:
    """PDF 바이너리를 PyMuPDF로 빠르게 스캔하여 DocumentProfile을 반환한다.

    이 함수는 파싱을 수행하지 않는다. 메타데이터만 읽어서
    어떤 파서를 써야 할지 판단하는 라우팅 데이터를 생성한다.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("pymupdf_not_installed, falling back to default profile")
        return DocumentProfile(
            complexity=DocumentComplexity.MIXED,
            recommended_parser="docforge",
        )

    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        total_pages = len(doc)

        if total_pages == 0:
            return DocumentProfile()

        text_pages = 0
        image_pages = 0
        image_only_pages = 0
        total_chars = 0
        total_images = 0
        has_tables = False
        total_image_area = 0.0
        total_page_area = 0.0

        for page in doc:
            page_rect = page.rect
            page_area = page_rect.width * page_rect.height
            total_page_area += page_area

            # 텍스트 추출 (빠른 모드)
            text = page.get_text("text")
            char_count = len(text.strip())
            total_chars += char_count

            # 이미지 목록
            images = page.get_images(full=True)
            img_count = len(images)
            total_images += img_count

            # 이미지 면적 계산 (bounding box 기반)
            page_image_area = 0.0
            for img in images:
                try:
                    img_rects = page.get_image_rects(img[0])
                    for rect in img_rects:
                        page_image_area += rect.width * rect.height
                except Exception:
                    pass
            total_image_area += page_image_area

            # 페이지 분류
            has_text = char_count >= _MIN_CHARS_PER_PAGE
            has_images = img_count > 0

            if has_text:
                text_pages += 1
            if has_images:
                image_pages += 1
            if has_images and not has_text:
                image_only_pages += 1

            # 표 감지: 텍스트에 표 구분자 패턴이 있는지
            if not has_tables and has_text:
                has_tables = _detect_table_hints(text)

    avg_chars = total_chars / total_pages if total_pages > 0 else 0.0
    image_area_ratio = total_image_area / total_page_area if total_page_area > 0 else 0.0

    # 복잡도 등급 + 추천 파서 결정
    complexity, parser = _determine_complexity(
        total_pages=total_pages,
        text_pages=text_pages,
        image_only_pages=image_only_pages,
        has_tables=has_tables,
        image_area_ratio=image_area_ratio,
        avg_chars=avg_chars,
    )

    profile = DocumentProfile(
        total_pages=total_pages,
        text_pages=text_pages,
        image_pages=image_pages,
        image_only_pages=image_only_pages,
        total_chars=total_chars,
        total_images=total_images,
        has_tables=has_tables,
        avg_chars_per_page=avg_chars,
        image_area_ratio=image_area_ratio,
        complexity=complexity,
        recommended_parser=parser,
    )

    logger.info(
        "pdf_analyzed",
        pages=total_pages,
        text_pages=text_pages,
        image_only_pages=image_only_pages,
        total_images=total_images,
        has_tables=has_tables,
        image_area_ratio=round(image_area_ratio, 3),
        complexity=complexity.value,
        recommended_parser=parser,
    )

    return profile


def _detect_table_hints(text: str) -> bool:
    """텍스트에서 표 구조 힌트를 감지한다.

    완벽한 표 감지가 아닌 빠른 휴리스틱.
    실제 표 추출은 DocForge가 담당한다.
    """
    lines = text.split("\n")
    consecutive_short = 0
    tab_lines = 0

    for line in lines:
        stripped = line.strip()

        # 표 구분자 문자 감지
        for keyword in _TABLE_LINE_KEYWORDS:
            if keyword in stripped:
                return True

        # 탭 구분 데이터 (CSV-like)
        if "\t" in stripped and len(stripped) > 5:
            tab_lines += 1
            if tab_lines >= 3:
                return True

        # 연속된 짧은 줄 (표의 셀이 줄바꿈으로 분리된 경우)
        if 1 < len(stripped) < 30:
            consecutive_short += 1
            if consecutive_short >= 5:
                return True
        else:
            consecutive_short = 0

    return False


def _determine_complexity(
    total_pages: int,
    text_pages: int,
    image_only_pages: int,
    has_tables: bool,
    image_area_ratio: float,
    avg_chars: float,
) -> tuple[DocumentComplexity, str]:
    """문서 복잡도와 추천 파서를 결정한다.

    핵심 원칙:
    - 텍스트만 있는 깨끗한 PDF → PyMuPDF (AI 파서에 넣으면 글자 변형 위험)
    - 표가 있으면 → DocForge (구조 보존)
    - 이미지 위주 → DocForge (OCR 위임)
    """
    image_only_ratio = image_only_pages / total_pages if total_pages > 0 else 0.0

    # Case 1: 이미지 위주 (스캔 문서)
    if image_only_ratio >= _IMAGE_HEAVY_RATIO:
        return DocumentComplexity.IMAGE_HEAVY, "docforge"

    # Case 2: 텍스트 + 이미지 혼합
    if image_only_pages > 0 and image_area_ratio > _IMAGE_AREA_TABLE_HINT:
        return DocumentComplexity.MIXED, "docforge"

    # Case 3: 텍스트 + 표
    if has_tables or image_area_ratio > _IMAGE_AREA_TABLE_HINT:
        return DocumentComplexity.TEXT_WITH_TABLES, "docforge"

    # Case 4: 순수 텍스트 — PyMuPDF가 최적
    # AI 파서에 넣으면 멀쩡한 글자가 변형될 수 있음
    return DocumentComplexity.TEXT_ONLY, "pymupdf"
