"""Local Chroma-backed retriever: the concrete impl behind the Retriever interface.

Two deliberate design choices:
  - Embeddings are INJECTED (the `embed` callable), not hard-wired. That keeps the embedding
    model swappable (local -> Voyage/OpenAI later) and lets tests run offline with a fake
    embedder, no model download.
  - The Chroma client is in-memory by default for the demo. Swap to
    chromadb.PersistentClient(path=...) for persistence, or a remote client for production,
    with no change anywhere else. This is the Dependency Inversion payoff in action.
"""

from __future__ import annotations

from typing import Callable
from uuid import UUID

from feynman_loop.retrieval.base import RetrievedPassage, Retriever
from feynman_loop.retrieval.chunking import ChunkConfig, chunk_document

# embed: takes a list of texts, returns one vector per text.
EmbeddingFn = Callable[[list[str]], list[list[float]]]


class ChromaRetriever(Retriever):
    def __init__(
        self,
        *,
        embed: EmbeddingFn,
        chunk_config: ChunkConfig | None = None,
        collection_name: str = "feynman",
        client=None,
    ) -> None:
        import chromadb  # WHY: imported here so the module loads even if chromadb is absent at import time

        self._client = client or chromadb.Client()
        # WHY: cosine space, because our embeddings are normalized and cosine is the right
        # similarity for semantic text. score = 1 - distance below reads as "higher is closer".
        self._collection = self._client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )
        self._embed = embed
        self._chunk_config = chunk_config or ChunkConfig()

    def ingest(self, *, doc_id: UUID, doc_label: str, text: str) -> None:
        chunks = chunk_document(text, self._chunk_config)
        if not chunks:
            return
        embeddings = self._embed(chunks)
        ids = [f"{doc_id}:{i}" for i in range(len(chunks))]
        # WHY: doc_id stored as str because Chroma metadata only accepts str/int/float/bool.
        metadatas = [{"doc_id": str(doc_id), "doc_label": doc_label} for _ in chunks]
        self._collection.add(ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas)

    def retrieve(self, *, query: str, k: int = 4) -> list[RetrievedPassage]:
        query_vec = self._embed([query])[0]
        res = self._collection.query(query_embeddings=[query_vec], n_results=k)

        documents = res["documents"][0]
        metadatas = res["metadatas"][0]
        distances = res.get("distances", [[None] * len(documents)])[0]

        passages: list[RetrievedPassage] = []
        for doc, meta, dist in zip(documents, metadatas, distances):
            passages.append(
                RetrievedPassage(
                    doc_id=UUID(meta["doc_id"]),
                    doc_label=meta["doc_label"],
                    text=doc,
                    score=(1.0 - dist) if dist is not None else None,
                )
            )
        return passages


def sentence_transformer_embedder(model_name: str = "all-MiniLM-L6-v2") -> EmbeddingFn:
    """The default local embedder. Lazily loads the model so importing this module is cheap;
    the model only loads (and downloads on first ever use) when you actually embed."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)

    def embed(texts: list[str]) -> list[list[float]]:
        return model.encode(texts, normalize_embeddings=True).tolist()

    return embed
