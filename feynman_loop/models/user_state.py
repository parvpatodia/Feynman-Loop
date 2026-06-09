"""The `user-state` bucket: what the system knows about ONE user on ONE concept, over time.

This is Decision 10. It drives the two jobs of the memory-of-understanding layer:
(a) detect a gap in an explanation, and (b) know when the concept is due (hybrid policy).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UserState(BaseModel):
    """One row per (user, concept) pair."""

    concept_id: UUID
    user_id: UUID

    # --- gap detection ---
    last_explanation: str | None = None  # what the user last said, in their own words
    identified_gaps: list[str] = Field(default_factory=list)  # what the judge found wrong/missing last time

    # WHY: the running grasp signal, 0..1. The judge reads it as context and writes it after each
    # review. It is also the input that modulates the spacing interval below (the "hybrid" part).
    understanding_level: float = 0.0

    # --- resurfacing (hybrid due policy, Decision 10) ---
    last_reviewed_at: datetime | None = None

    # WHY: the ONLY field the scheduler reads to decide candidacy. It is written ONLY at the end
    # of a review, by the interval logic. The scheduler never mutates it (Decision 6 + 10:
    # the system suggests "due", it never imposes it).
    next_due_at: datetime | None = None

    review_count: int = 0

    # DESIGN NOTE (open, for Parv to confirm): the hybrid interval is currently assumed to be
    # recomputed each review from understanding_level + last_reviewed_at, so no separate
    # interval/ease field is stored. If you later want SM-2-style compounding (where ease itself
    # grows across many clean reviews), that needs an added `ease_factor` field here.
