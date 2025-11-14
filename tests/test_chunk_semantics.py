from repocapsule.chunk import ChunkPolicy, chunk_text


def _make_paragraph(prefix: str, count: int) -> str:
    return " ".join(f"{prefix} sentence {i} is here." for i in range(count))


def test_semantic_doc_splits_large_run_on_block() -> None:
    paragraph = _make_paragraph("Intro", 120)
    text = paragraph + "\n\n" + paragraph
    base_policy = ChunkPolicy(
        mode="doc",
        target_tokens=140,
        overlap_tokens=0,
        min_tokens=60,
        semantic_doc=False,
    )
    semantic_policy = ChunkPolicy(
        mode="doc",
        target_tokens=140,
        overlap_tokens=0,
        min_tokens=60,
        semantic_doc=True,
        semantic_tokens_per_block=70,
    )

    default_chunks = chunk_text(text, mode="doc", fmt="markdown", policy=base_policy)
    semantic_chunks = chunk_text(text, mode="doc", fmt="markdown", policy=semantic_policy)

    assert len(default_chunks) == 1  # single oversized block without semantic hints
    assert len(semantic_chunks) > 1  # semantic mode provides extra boundaries


def test_heading_prefers_new_chunk_boundary() -> None:
    intro = _make_paragraph("Intro", 90)
    section = _make_paragraph("Section", 90)
    text = intro + "\n\n## Second Section\n\n" + section
    policy = ChunkPolicy(
        mode="doc",
        target_tokens=200,
        overlap_tokens=0,
        min_tokens=80,
        semantic_doc=False,
    )

    chunks = chunk_text(text, mode="doc", fmt="markdown", policy=policy)

    assert len(chunks) >= 2
    assert "## Second Section" not in chunks[0]["text"]
    assert chunks[1]["text"].lstrip().startswith("## Second Section")
