"""Chunker 테스트."""

from src.pipeline.chunker import MarkdownChunker, TextChunker


def test_text_chunker_small_doc():
    chunker = TextChunker(chunk_size=100, chunk_overlap=20)
    chunks = chunker.split("짧은 문서입니다.")
    assert len(chunks) == 1
    assert chunks[0]["chunkIndex"] == 0
    assert chunks[0]["content"] == "짧은 문서입니다."


def test_text_chunker_splits():
    chunker = TextChunker(chunk_size=50, chunk_overlap=0)
    text = "A" * 30 + "\n\n" + "B" * 30 + "\n\n" + "C" * 30
    chunks = chunker.split(text)
    assert len(chunks) >= 2


def test_text_chunker_token_count():
    chunker = TextChunker(chunk_size=1000, chunk_overlap=0)
    chunks = chunker.split("Hello world. This is a test.")
    assert all("tokenCount" in c for c in chunks)
    assert all(c["tokenCount"] > 0 for c in chunks)


def test_markdown_chunker():
    chunker = MarkdownChunker(chunk_size=200, chunk_overlap=0)
    text = "# 제목1\n내용1\n\n# 제목2\n내용2\n\n# 제목3\n내용3"
    chunks = chunker.split(text)
    assert len(chunks) >= 2


# ---- AST-lite: 섹션 계층 메타데이터 (chunk.metadata.section_path) ----


def test_markdown_chunker_section_path_tracks_hierarchy():
    """헤딩 트리가 각 청크의 section_path(루트→현재)로 복원된다."""
    from src.pipeline.chunker import MarkdownChunker

    md = (
        "# 제1장 총칙\n본문A\n"
        "## 제1절 목적\n본문B\n"
        "## 제2절 정의\n본문C\n"
        "# 제2장 보상\n본문D\n"
    )
    chunks = MarkdownChunker(chunk_size=1000).split(md)
    paths = [c["metadata"].get("section_path") for c in chunks]

    assert paths[0] == ["제1장 총칙"]
    assert paths[1] == ["제1장 총칙", "제1절 목적"]
    assert paths[2] == ["제1장 총칙", "제2절 정의"]  # 형제 절 — 이전 절이 pop 됨
    assert paths[3] == ["제2장 보상"]  # 상위 장 전환 — 스택 리셋
    assert chunks[1]["metadata"]["section_level"] == 2


def test_markdown_chunker_large_section_parts_inherit_path():
    """chunk_size를 넘는 섹션의 분할 청크들도 같은 section_path를 상속한다."""
    from src.pipeline.chunker import MarkdownChunker

    md = "# 제1장\n" + ("문장. " * 500)  # 강제 분할 유도
    chunks = MarkdownChunker(chunk_size=300, chunk_overlap=30).split(md)

    assert len(chunks) > 1
    for i, c in enumerate(chunks):
        assert c["metadata"]["section_path"] == ["제1장"]
        assert c["metadata"]["section_part"] == i


def test_markdown_chunker_preamble_without_heading_has_empty_metadata():
    """선두 헤딩 이전의 프리앰블 청크는 섹션 메타 없이 저장된다(빈 dict)."""
    from src.pipeline.chunker import MarkdownChunker

    md = "머리말 텍스트\n\n# 제1장\n본문\n"
    chunks = MarkdownChunker(chunk_size=1000).split(md)

    assert chunks[0]["metadata"] == {}
    assert chunks[1]["metadata"]["section_path"] == ["제1장"]
