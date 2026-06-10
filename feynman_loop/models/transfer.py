"""Transfer-measurement data model (Decision 12).

Transfer = can the user APPLY the concept to a case they were not shown, not restate it.
A probe is a novel application question plus a rubric, where every rubric point is grounded in
the source (reuses Citation), so scoring is never against invented truth.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from feynman_loop.models.gap_report import Citation


class RubricPoint(BaseModel):
    """One thing a correct answer must contain, grounded in the source."""

    criterion: str
    citation: Citation  # WHY: the rubric point must trace to the source, or we'd grade on invention


class TransferProbe(BaseModel):
    """A generated transfer challenge: the question plus its grounded rubric."""

    concept_id: UUID
    question: str
    rubric: list[RubricPoint]


class TransferResult(BaseModel):
    """The outcome of scoring a user's answer against the rubric."""

    concept_id: UUID
    question: str
    user_answer: str
    transfer_score: float                      # fraction of rubric points met, 0..1
    met: list[str] = Field(default_factory=list)        # criteria the answer satisfied
    missed: list[RubricPoint] = Field(default_factory=list)  # missed, still grounded
