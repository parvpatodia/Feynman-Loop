"""End-to-end test of the orchestration loop with fake retriever + judge (offline, no tokens)."""

from uuid import uuid4

from feynman_loop.loop import run_review
from feynman_loop.models import (
    Citation,
    Concept,
    Gap,
    GapReport,
    SourceRef,
    SourceTier,
)
from feynman_loop.retrieval.base import RetrievedPassage
from feynman_loop.storage import JsonUserStateStore


class _FakeRetriever:
    def __init__(self, passages):
        self._passages = passages
        self.queries = []

    def ingest(self, *, doc_id, doc_label, text):
        pass

    def retrieve(self, *, query, k=4):
        self.queries.append(query)
        return self._passages


class _FakeJudge:
    def __init__(self, report):
        self._report = report

    def evaluate(self, *, concept, user_explanation, passages):
        return self._report


def _concept():
    return Concept(
        label="Backpropagation",
        source_ref=SourceRef(
            tier=SourceTier.UPLOADED,
            doc_id=uuid4(),
            doc_label="Goodfellow Ch.6",
            retrieval_query="backprop: chain rule over the computational graph",
        ),
    )


def _report(concept_id, level):
    return GapReport(
        concept_id=concept_id,
        user_explanation="...",
        understanding_level=level,
        correct_points=["gradients flow backward"],
        gaps=[
            Gap(
                description="missed the chain rule",
                citation=Citation(doc_label="Goodfellow Ch.6", quote="chain rule recursively"),
            )
        ],
    )


def test_run_review_retrieves_with_concept_query_and_persists(tmp_path):
    c = _concept()
    user_id = uuid4()
    passages = [RetrievedPassage(doc_id=c.source_ref.doc_id, doc_label="Goodfellow Ch.6", text="...")]
    retriever = _FakeRetriever(passages)
    judge = _FakeJudge(_report(c.id, 0.5))
    store = JsonUserStateStore(tmp_path / "state.json")

    report, state = run_review(
        concept=c,
        user_id=user_id,
        explanation="backprop updates weights",
        retriever=retriever,
        judge=judge,
        store=store,
    )

    # retrieved using the concept's stored query, not the user's words (Decision 13)
    assert retriever.queries == ["backprop: chain rule over the computational graph"]
    # user-state captured the review and computed a due date (written by the review, Decision 10)
    assert state.understanding_level == 0.5
    assert state.next_due_at is not None
    assert state.last_explanation == "backprop updates weights"
    assert state.identified_gaps == ["missed the chain rule"]
    assert state.review_count == 1
    # persisted and reloadable
    assert store.get(user_id=user_id, concept_id=c.id).review_count == 1


def test_second_review_increments_count(tmp_path):
    c = _concept()
    user_id = uuid4()
    passages = [RetrievedPassage(doc_id=c.source_ref.doc_id, doc_label="Goodfellow Ch.6", text="...")]
    retriever = _FakeRetriever(passages)
    store = JsonUserStateStore(tmp_path / "state.json")

    run_review(concept=c, user_id=user_id, explanation="a", retriever=retriever,
               judge=_FakeJudge(_report(c.id, 0.3)), store=store)
    _, state = run_review(concept=c, user_id=user_id, explanation="b", retriever=retriever,
                          judge=_FakeJudge(_report(c.id, 0.7)), store=store)

    assert state.review_count == 2  # prior review was loaded from the store and incremented
