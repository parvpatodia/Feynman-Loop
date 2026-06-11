"""The `concept` bucket: the atom the whole system operates on.

A concept stores WHERE its truth lives (a locator), never the truth text itself.
This is Decision 9. The locator lets the judge retrieve the relevant passage LIVE at
judge time, which avoids the staleness that got fine-tuning ruled out in Decision 7.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from feynman_loop.models.gap_report import RubricPoint

# The depth the learner is aiming for. EXPLICIT, never silently inferred: a beginner explains
# shallowly because they are a beginner, not because they want shallow standards, so inferring
# depth from style would lower the bar exactly when it should hold. The user sets the target
# (per the constitution); the learner profile may SUGGEST a change, openly, later.
Depth = Literal["overview", "working", "expert"]


def _utcnow() -> datetime:
    # WHY: timezone-aware UTC. datetime.utcnow() is deprecated and returns a naive datetime,
    # which silently breaks comparisons against aware timestamps in the scheduler.
    return datetime.now(timezone.utc)


class SourceTier(str, Enum):
    """The trust ordering for where a concept's truth comes from (Decision 7).

    A lower tier must never overrule a higher one. This ordering IS the trust design:
    a course-specific answer in the user's own material must win over the model's generic one.
    """

    UPLOADED = "uploaded"              # user's own material: highest authority
    CORPUS = "corpus"                  # the curated retrieval corpus we own and grow
    MODEL_FALLBACK = "model_fallback"  # base-model knowledge, flagged low-confidence


class SourceRef(BaseModel):
    """A LOCATOR to the truth, not the truth (Decision 9)."""

    tier: SourceTier

    # WHY: doc_id is the stable key we actually retrieve against. It is None ONLY for
    # MODEL_FALLBACK, which has no document to point at.
    doc_id: UUID | None = None

    # WHY: display-only label ("Goodfellow Ch.6") shown to the user as "judged against: ...".
    # It survives file renames precisely because it is never used as a key.
    doc_label: str | None = None

    # WHY: the query RAG embeds at judge time to pull the passage relevant to what the user
    # actually said. This is how we get from a 700-page doc down to the 3 backprop paragraphs
    # WITHOUT storing a brittle page span that breaks when the doc changes.
    retrieval_query: str


class Concept(BaseModel):
    """The single concept. Stores its locator; never its goals (those live in the link)."""

    id: UUID = Field(default_factory=uuid4)   # WHY: stable internal key, never the display name
    label: str                                # human-readable ("Backpropagation"), display only
    source_ref: SourceRef
    # WHY: the key points a correct explanation must cover, built ONCE from the source at setup and
    # reused for every review, so the understanding score is consistent and responsive across attempts.
    rubric: list[RubricPoint] = Field(default_factory=list)
    # WHY: neighbouring concepts (prerequisites/siblings), fetched once at intake. These become the
    # edges of the knowledge graph; untracked neighbours render as the learner's frontier.
    related: list[str] = Field(default_factory=list)
    # WHY: the depth the rubric is built to. Changing depth rebuilds the rubric (scores across
    # depths are not comparable, so the change is explicit, never silent).
    depth: Depth = "working"
    # WHY a snapshot despite Decision 9 (locator, not truth): an MCP source is an ephemeral paste
    # from the chat, not a document on disk, so no locator can re-find it after a restart. The
    # capped snapshot is what keeps grounding alive across restarts, rebuilds, and depth changes.
    # Stays local (SQLite); empty for doc-backed or knowledge-only concepts.
    source_text: str = ""
    created_at: datetime = Field(default_factory=_utcnow)

    # NOTE: there is deliberately no goal_id here. Decision 11 moved the concept->goal tie into
    # the RelevanceLink join table (many-to-many), so a concept can serve several goals without
    # being duplicated. The concept does not know its own goals; the link owns that relationship.
