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
