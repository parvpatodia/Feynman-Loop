"""The retrieval boundary.

This is Dependency Inversion (the D in the project's OOP rules). The orchestration and the
judge depend on THIS interface, never on Chroma / pgvector / FAISS directly. That is how we
get production-shaped code at demo cost: start with a trivial local impl, and swap to a
production store later by writing one new subclass, with zero changes anywhere else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import BaseModel


class RetrievedPassage(BaseModel):
    """A chunk RAG pulled back for a query. The judge turns these into grounded Citations."""

    doc_id: UUID
    doc_label: str
    text: str
    score: float | None = None  # similarity score if the store provides one


class Retriever(ABC):
    """The contract every vector store must satisfy. Demo uses a local impl; production
    swaps in another behind this same interface."""

    @abstractmethod
    def ingest(self, *, doc_id: UUID, doc_label: str, text: str) -> None:
        """Chunk, embed, and index one source document so it can be retrieved later."""

    @abstractmethod
    def retrieve(self, *, query: str, k: int = 4) -> list[RetrievedPassage]:
        """Return the top-k passages most relevant to the query."""
