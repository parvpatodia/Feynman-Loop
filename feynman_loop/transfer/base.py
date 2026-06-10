"""The transfer boundary (same Dependency-Inversion idea as Retriever/Judge).

Two responsibilities: generate a grounded probe, and score an answer against it. The orchestration
depends on this interface, not on Anthropic, so the engine is swappable and testable offline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from feynman_loop.models import Concept, TransferProbe, TransferResult
from feynman_loop.retrieval.base import RetrievedPassage


class TransferEngine(ABC):
    @abstractmethod
    def generate_probe(
        self, *, concept: Concept, passages: list[RetrievedPassage]
    ) -> TransferProbe:
        """Generate a novel application question + a rubric grounded in the passages."""

    @abstractmethod
    def score_answer(self, *, probe: TransferProbe, user_answer: str) -> TransferResult:
        """Score the answer against the rubric criteria; return per-criterion met/missed."""
