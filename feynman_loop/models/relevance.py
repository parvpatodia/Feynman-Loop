"""The `relevance-link` bucket: keeps every concept tied to a live goal.

This is Decision 11 and it enforces Decision 6 (relevance is filtered at intake). A `goal`
is the live reason a concept was let in; a `RelevanceLink` is the many-to-many tie between them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GoalType(str, Enum):
    EXAM = "exam"
    PROJECT = "project"
    PAPER = "paper"
    OTHER = "other"


class GoalStatus(str, Enum):
    ACTIVE = "active"        # WHY: scheduler only surfaces concepts linked to an ACTIVE goal
    ARCHIVED = "archived"    # archived -> its concepts go quiet: not deleted, not penalized (Principle 6)


class Goal(BaseModel):
    """The live reason a concept exists in the system (a paper, a project, an exam)."""

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    label: str
    type: GoalType = GoalType.OTHER
    status: GoalStatus = GoalStatus.ACTIVE
    created_at: datetime = Field(default_factory=_utcnow)

    # NOTE: deadline deliberately omitted from v1 (Decision 11). Deadline-weighted scheduling is
    # real logic we have not earned yet; add a `deadline` field only when the product needs it.


class RelevanceLink(BaseModel):
    """Many-to-many tie between a concept and a goal (Decision 11).

    The scheduler rule that depends on this: a concept is a due-candidate only if it links to
    at least one goal whose status == ACTIVE.
    """

    concept_id: UUID
    goal_id: UUID
    created_at: datetime = Field(default_factory=_utcnow)
