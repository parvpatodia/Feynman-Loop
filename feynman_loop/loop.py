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


def run_review(
    *,
    concept: Concept,
    user_id: UUID,
    explanation: str,
    retriever: Retriever,
    judge: Judge,
    store: JsonUserStateStore | None = None,
    now: datetime | None = None,
    k: int = 4,
) -> tuple[GapReport, UserState]:
    now = now or datetime.now(timezone.utc)

    # 1. RETRIEVE. The query is the concept's stored retrieval_query (Decision 13), not the
    #    user's words — we want the canonical passage for this concept.
    passages = retriever.retrieve(query=concept.source_ref.retrieval_query, k=k)

    # 2. JUDGE. Compares the explanation ONLY against those passages. Raises if there are none
    #    (Decision 15: never judge an ungrounded concept).
    report = judge.evaluate(concept=concept, user_explanation=explanation, passages=passages)

    # 3. UPDATE USER-STATE. This is where next_due_at is written — at the END of the review,
    #    by the interval logic. The scheduler never writes it; it only reads it later to say
    #    "this is due". That is what makes "due" a suggestion, not something imposed (Decision 10).
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
    retriever: Retriever,
    engine: TransferEngine,
    k: int = 4,
) -> TransferProbe:
    """Retrieve the grounding passages, then generate a grounded transfer challenge."""
    passages = retriever.retrieve(query=concept.source_ref.retrieval_query, k=k)
    return engine.generate_probe(concept=concept, passages=passages)


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
    retriever: Retriever,
    engine: TransferEngine,
    missed: list[RubricPoint],
    k: int = 4,
) -> TransferProbe:
    """A narrower retry focused on the points the learner missed. The caller bounds it to one."""
    passages = retriever.retrieve(query=concept.source_ref.retrieval_query, k=k)
    return engine.generate_remediation(concept=concept, passages=passages, missed=missed)
