"""The explain-it-back orchestration loop. This is the glue that wires the pieces together.

One review = retrieve the grounding -> judge the explanation against it -> write what we learned
about the user into user-state. This is the whole core interaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from feynman_loop.judge.base import Judge
from feynman_loop.models import Concept, GapReport, RubricPoint, TransferProbe, TransferResult, UserState
from feynman_loop.retrieval.base import Retriever
from feynman_loop.scheduling import compute_next_due
from feynman_loop.storage import JsonUserStateStore
from feynman_loop.transfer.base import TransferEngine

# WHY: don't probe application until the baseline explanation is solid; testing transfer on
# someone who can't even restate the concept measures nothing (Decision 12).
TRANSFER_GATE = 0.6

# WHY: if a transfer is weak, offer ONE narrower retry focused on the gap (bounded, not a loop).
REMEDIATION_GATE = 0.6


def _passages(concept: Concept, retriever: Retriever | None, k: int):
    # WHY: retriever is None for tier-3 (no source). Empty passages -> the judge/transfer build
    # from the model's own knowledge (flagged), so one code path serves grounded and no-source.
    return retriever.retrieve(query=concept.source_ref.retrieval_query, k=k) if retriever else []


def build_concept_rubric(
    *,
    concept: Concept,
    retriever: Retriever | None,
    judge: Judge,
    k: int = 4,
) -> None:
    """Build the concept's fixed scoring rubric ONCE at setup and store it on the concept. With a
    source (retriever) the rubric is grounded in it; with no source it's built from model knowledge."""
    concept.rubric = judge.build_rubric(concept=concept, passages=_passages(concept, retriever, k))


def run_review(
    *,
    concept: Concept,
    user_id: UUID,
    explanation: str,
    judge: Judge,
    store: JsonUserStateStore | None = None,
    now: datetime | None = None,
) -> tuple[GapReport, UserState]:
    now = now or datetime.now(timezone.utc)

    # JUDGE. Score the explanation against the concept's fixed rubric (built once at setup). The
    # understanding score is computed from per-point statuses, so it is accurate and responsive.
    report = judge.evaluate(concept=concept, user_explanation=explanation)

    # UPDATE USER-STATE. next_due_at is written HERE, at the END of the review, by the interval
    # logic. The scheduler only reads it later (Decision 10: "due" is a suggestion, not imposed).
    prior = store.get(user_id=user_id, concept_id=concept.id) if store else None
    state = UserState(
        concept_id=concept.id,
        user_id=user_id,
        last_explanation=explanation,
        identified_gaps=[g.description for g in report.gaps],
        understanding_level=report.understanding_level,
        last_reviewed_at=now,
        next_due_at=compute_next_due(report.understanding_level, now=now),
        review_count=(prior.review_count + 1) if prior else 1,
    )

    if store is not None:
        store.put(state)

    return report, state


def generate_transfer_probe(
    *,
    concept: Concept,
    retriever: Retriever | None,
    engine: TransferEngine,
    k: int = 4,
) -> TransferProbe:
    """Generate a transfer challenge, grounded in the source if there is one, else from knowledge."""
    return engine.generate_probe(concept=concept, passages=_passages(concept, retriever, k))


def score_transfer(
    *,
    probe: TransferProbe,
    user_id: UUID,
    user_answer: str,
    engine: TransferEngine,
    store: JsonUserStateStore | None = None,
    now: datetime | None = None,
) -> TransferResult:
    """Score the answer against the grounded rubric, record transfer_level, and pull the concept
    back sooner when transfer is weak."""
    now = now or datetime.now(timezone.utc)
    result = engine.score_answer(probe=probe, user_answer=user_answer)
    if store is not None:
        state = store.get(user_id=user_id, concept_id=probe.concept_id)
        if state is not None:
            # WHY: transfer_level is its own signal (restating and applying are different things).
            state.transfer_level = result.transfer_score
            # WHY: weakest-link. The lower of (can-restate, can-apply) governs when it resurfaces,
            # so a poor transfer overrides a good explanation and brings the concept back soon.
            effective = min(state.understanding_level, result.transfer_score)
            state.next_due_at = compute_next_due(effective, now=now)
            store.put(state)
    return result


def generate_remediation_probe(
    *,
    concept: Concept,
    retriever: Retriever | None,
    engine: TransferEngine,
    missed: list[RubricPoint],
    k: int = 4,
) -> TransferProbe:
    """A narrower retry focused on the points the learner missed. The caller bounds it to one."""
    return engine.generate_remediation(concept=concept, passages=_passages(concept, retriever, k), missed=missed)
