"""The judging boundary.

Keeps the LLM the smallest commodity component (Principles §4). The system depends on this
interface, not on Anthropic. Two responsibilities: build the concept's fixed scoring rubric once
from the source, then score an explanation against that rubric.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from feynman_loop.models import Concept, GapReport, RubricPoint
from feynman_loop.retrieval.base import RetrievedPassage


class Judge(ABC):
    @abstractmethod
    def build_rubric(
        self, *, concept: Concept, passages: list[RetrievedPassage]
    ) -> list[RubricPoint]:
        """Derive the fixed key points a correct explanation must cover (once per concept)."""

    @abstractmethod
    def evaluate(self, *, concept: Concept, user_explanation: str) -> GapReport:
        """Score the explanation against concept.rubric; return a grounded GapReport."""
