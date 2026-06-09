"""Retriever wiring tests. A fake, deterministic embedder keeps these offline and fast,
no model download. The fake embeds over a tiny fixed vocabulary so the relevant chunk
genuinely ranks first, which tests the ingest->retrieve path end to end."""

import math
from uuid import uuid4

from feynman_loop.retrieval.chunking import ChunkConfig
from feynman_loop.retrieval.chroma_store import ChromaRetriever

_VOCAB = ["backprop", "chain", "rule", "gradient", "convolution", "kernel", "image"]


def _fake_embed(texts):
    vecs = []
    for t in texts:
        words = t.lower().split()
        v = [float(words.count(term)) for term in _VOCAB]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        vecs.append([x / norm for x in v])
    return vecs


def _retriever():
    # small cap + no overlap so each short paragraph becomes its own chunk
    return ChromaRetriever(
        embed=_fake_embed,
        chunk_config=ChunkConfig(max_words=6, overlap_words=0),
        collection_name=f"test_{uuid4().hex}",
    )


def test_retrieve_returns_most_relevant_chunk_first():
    r = _retriever()
    doc_id = uuid4()
    text = "backprop uses the chain rule gradient\n\nconvolution applies a kernel over image"
    r.ingest(doc_id=doc_id, doc_label="Goodfellow Ch.6", text=text)

    passages = r.retrieve(query="backprop chain rule gradient", k=4)

    assert len(passages) == 2  # two chunks ingested
    assert "backprop" in passages[0].text          # the relevant chunk ranks first
    assert passages[0].doc_label == "Goodfellow Ch.6"
    assert passages[0].doc_id == doc_id            # round-trips through str metadata


def test_empty_doc_ingests_nothing():
    r = _retriever()
    r.ingest(doc_id=uuid4(), doc_label="empty", text="   \n\n  ")
    assert r.retrieve(query="anything", k=4) == []
