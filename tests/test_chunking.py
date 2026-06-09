from feynman_loop.retrieval.chunking import ChunkConfig, chunk_document


def test_short_doc_is_one_chunk():
    text = "Backprop applies the chain rule. It updates weights using gradients."
    chunks = chunk_document(text, ChunkConfig(max_words=200, overlap_words=20))
    assert len(chunks) == 1


def test_respects_cap():
    # 10 paragraphs of 50 words each, cap 120 -> must produce several chunks under the cap
    para = " ".join(["word"] * 50)
    text = "\n\n".join([para] * 10)
    chunks = chunk_document(text, ChunkConfig(max_words=120, overlap_words=20))
    assert len(chunks) > 1
    # WHY: allow a small slack because the overlap prefix is added on top of packed segments.
    for c in chunks:
        assert len(c.split()) <= 120 + 50


def test_overlap_carried_between_chunks():
    p1 = " ".join(f"alpha{i}" for i in range(60))
    p2 = " ".join(f"beta{i}" for i in range(60))
    text = p1 + "\n\n" + p2
    chunks = chunk_document(text, ChunkConfig(max_words=70, overlap_words=10))
    assert len(chunks) >= 2
    # the tail of chunk 0 should reappear at the head of chunk 1
    tail = chunks[0].split()[-10:]
    assert any(w in chunks[1].split() for w in tail)


def test_oversized_single_sentence_is_hard_split():
    # one sentence, no paragraph or sentence breaks, longer than the cap
    text = " ".join(["x"] * 300)
    chunks = chunk_document(text, ChunkConfig(max_words=100, overlap_words=0))
    assert len(chunks) >= 3


def test_empty_doc_yields_no_chunks():
    assert chunk_document("   \n\n   ") == []
