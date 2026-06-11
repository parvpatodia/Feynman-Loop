"""Derive the retrieval query from a concept name, so the USER never writes a search string.

A learner testing "IPO" should type "IPO", not engineer the keywords that retrieve well. The
expander turns the concept name into a richer semantic query (e.g. "IPO" -> "initial public
offering: a company first selling shares to the public"), which retrieves the right passage even
when the source uses different words.

Uses Haiku, not Opus, and no thinking: this is a trivial, latency-sensitive subtask that runs at
session start, fast and cheap matters more than depth here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from anthropic import Anthropic
from pydantic import BaseModel

from feynman_loop.providers import fast_model

_SYSTEM = """Given the name of a concept, write ONE concise retrieval query that captures the
concept's key terms and meaning, for finding the passage that explains it in a document via
semantic search. Include common synonyms or the expanded form. Output only the query, under ~20
words, no preamble."""


class QueryExpander(ABC):
    @abstractmethod
    def expand(self, *, concept_label: str) -> str:
        """Return a retrieval query derived from the concept name."""


class _Expansion(BaseModel):
    query: str


class ClaudeQueryExpander(QueryExpander):
    def __init__(self, *, client: Anthropic | None = None, model: str | None = None) -> None:
        self._client = client or Anthropic()
        self._model = model or fast_model()

    def expand(self, *, concept_label: str) -> str:
        draft: _Expansion = self._client.messages.parse(
            model=self._model,
            max_tokens=256,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Concept: {concept_label}"}],
            output_format=_Expansion,
        ).parsed_output
        # WHY: fall back to the raw label if the model returns nothing, never produce an empty query.
        return draft.query.strip() or concept_label
