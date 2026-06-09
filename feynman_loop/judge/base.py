"""The judging boundary.

Keeps the LLM the smallest commodity component (Principles §4). The system depends on this
interface, not on Anthropic. Swapping the provider, or even moving judgment off an LLM later,
means writing one new Judge subclass with zero changes elsewhere. This is the line that keeps
the project out of wrapper territory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from feynman_loop.models import Concept, GapReport
from feynman_loop.retrieval.base import RetrievedPassage


class Judge(ABC):
    """Compares a user's explanation against retrieved ground truth and returns a grounded report."""

    @abstractmethod
    def evaluate(
        self,
        *,
        concept: Concept,
        user_explanation: str,
        passages: list[RetrievedPassage],
    ) -> GapReport:
        """Judge the explanation against the passages; return a GapReport with every gap cited."""
