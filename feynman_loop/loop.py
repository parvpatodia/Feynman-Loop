"""The explain-it-back orchestration loop. This is the glue that wires the pieces together.

One review = retrieve the grounding -> judge the explanation against it -> write what we learned
about the user into user-state. This is the whole core interaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from feynman_loop.judge.base import Judge
from feynman_loop.models import Concept, GapReport, UserState
from feynman_loop.retrieval.base import Retriever
from feynman_loop.scheduling import compute_next_due
from feynman_loop.storage import JsonUserStateStore


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
