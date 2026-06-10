"""The judge's structured output: the demo beat's result.

This is the contract the UI renders. It encodes exactly what the user must see (Parv's spec):
what they got right, where they went wrong, and the ground-truth quote backing every claim.
Free text won't do, the trust design (Learnings) requires every gap to carry its grounding.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

# Shown as the "source" for tier-3 rubric points built from the model's own knowledge
# (Decision 15 option b), flagged lower-confidence because there is no user source to ground in.
MODEL_FALLBACK_LABEL = "general knowledge (unverified)"


class Citation(BaseModel):
    """A pointer back into the user's OWN source. Transparency is the whole point."""

    doc_label: str            # display, e.g. "Goodfellow Ch.6"
    doc_id: UUID | None = None
    quote: str                # the exact retrieved passage the judge relied on


class RubricPoint(BaseModel):
    """A grounded criterion: one idea a correct explanation or answer must contain."""

    criterion: str
    citation: Citation  # WHY: every criterion traces to the source, or we'd grade on invention


class Gap(BaseModel):
    """One specific thing the user got wrong or skipped."""

    description: str          # plain-language: what is missing or wrong
    # WHY: a gap with no citation is an ungrounded "you're wrong", which the trust criterion
    # says breaks trust permanently. Grounding is mandatory, so citation is not optional.
    citation: Citation


class GapReport(BaseModel):
    """What the judge returns and the UI renders. Written back into UserState after a review."""

    concept_id: UUID
    user_explanation: str                       # echoed back so the user sees what was judged

    # WHY: the judge's grasp score, 0..1. Feeds UserState.understanding_level and the hybrid interval.
    understanding_level: float

    # WHY: affirm what was correct, not just list failures. The pedagogy rewards productive
    # wrongness; a purely punitive report trains the user to avoid hard concepts.
    correct_points: list[str] = Field(default_factory=list)

    gaps: list[Gap] = Field(default_factory=list)  # each one grounded in a Citation

    # NOTE: no auto-generated follow-up question in v1. The beat is the grounded gap. Adding a
    # question-generator here would drift toward the "surface the question you didn't ask"
    # interaction we explicitly did NOT choose (the core interaction is explain-it-back).
