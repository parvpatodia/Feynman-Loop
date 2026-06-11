"""Orchestration loop tests with fakes (offline, no tokens)."""

from uuid import uuid4

from feynman_loop.loop import build_concept_rubric, run_review
from feynman_loop.models import (
    Citation,
    Concept,
    Gap,
    GapReport,
    RubricPoint,
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
    def __init__(self, report=None, rubric=None):
        self._report = report
        self._rubric = rubric or []

    def build_rubric(self, *, concept, passages):
        return self._rubric

    def evaluate(self, *, concept, user_explanation):
        return self._report


def _concept():
    return Concept(
        label="Backpropagation",
        source_ref=SourceRef(tier=SourceTier.UPLOADED, doc_id=uuid4(), doc_label="Goodfellow Ch.6",
                             retrieval_query="backprop: chain rule over the computational graph"),
    )


def _report(concept_id, level):
    return GapReport(
        concept_id=concept_id, user_explanation="...", understanding_level=level,
        correct_points=["gradients flow backward"],
        gaps=[Gap(description="What happens at each layer?",
                  citation=Citation(doc_label="Goodfellow Ch.6", quote="chain rule recursively"))],
    )


def test_build_concept_rubric_uses_concept_query_and_sets_rubric():
    c = _concept()
    rubric = [RubricPoint(criterion="uses chain rule", citation=Citation(doc_label="Goodfellow Ch.6", quote="q"))]
    retriever = _FakeRetriever([RetrievedPassage(doc_id=c.source_ref.doc_id, doc_label="Goodfellow Ch.6", text="...")])
    build_concept_rubric(concept=c, retriever=retriever, judge=_FakeJudge(rubric=rubric))
    assert retriever.queries == ["backprop: chain rule over the computational graph"]  # retrieves by the concept query
    assert c.rubric == rubric


def test_build_concept_rubric_without_source_uses_knowledge():
    # tier-3: retriever=None -> no retrieval -> judge builds the rubric from model knowledge
    c = _concept()
    rubric = [RubricPoint(criterion="x", citation=Citation(doc_label="general knowledge (unverified)", quote="q"))]
    build_concept_rubric(concept=c, retriever=None, judge=_FakeJudge(rubric=rubric))
    assert c.rubric == rubric


def test_run_review_judges_and_persists(tmp_path):
    c = _concept()
    user_id = uuid4()
    judge = _FakeJudge(report=_report(c.id, 0.5))
    store = JsonUserStateStore(tmp_path / "state.json")

    report, state, rehearsed = run_review(concept=c, user_id=user_id,
                                          explanation="backprop updates weights",
                                          judge=judge, store=store)
    assert rehearsed is False  # first attempt can't be a rehearsal

    assert state.understanding_level == 0.5
    assert state.next_due_at is not None
    assert state.last_explanation == "backprop updates weights"
    assert state.identified_gaps == ["What happens at each layer?"]
    assert state.review_count == 1
    assert store.get(user_id=user_id, concept_id=c.id).review_count == 1


def test_second_review_increments_count(tmp_path):
    c = _concept()
    user_id = uuid4()
    store = JsonUserStateStore(tmp_path / "state.json")
    run_review(concept=c, user_id=user_id, explanation="a", judge=_FakeJudge(report=_report(c.id, 0.3)), store=store)
    _, state, _ = run_review(concept=c, user_id=user_id, explanation="b", judge=_FakeJudge(report=_report(c.id, 0.7)), store=store)
    assert state.review_count == 2
