"""ClaudeJudge tests. Fake client, offline, no tokens. Verifies the rubric is grounded by index,
and that the understanding score is computed in code from per-point statuses (not a model guess)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from feynman_loop.judge.claude_judge import (
    ClaudeJudge,
    _CriterionStatus,
    _RubricDraft,
    _RubricItem,
    _ScoreDraft,
)
from feynman_loop.models import Citation, Concept, RubricPoint, SourceRef, SourceTier
from feynman_loop.retrieval.base import RetrievedPassage


class _FakeMessages:
    def __init__(self, rubric_draft=None, score_draft=None):
        self.rubric_draft = rubric_draft
        self.score_draft = score_draft
        self.calls = []

    def parse(self, **kw):
        self.calls.append(kw)
        if kw["output_format"].__name__ == "_RubricDraft":
            return SimpleNamespace(parsed_output=self.rubric_draft)
        return SimpleNamespace(parsed_output=self.score_draft)


class _FakeClient:
    def __init__(self, rubric_draft=None, score_draft=None):
        self.messages = _FakeMessages(rubric_draft, score_draft)


def _concept(rubric=None):
    return Concept(
        label="Backpropagation",
        source_ref=SourceRef(tier=SourceTier.UPLOADED, doc_id=uuid4(),
                             doc_label="Goodfellow Ch.6", retrieval_query="backprop"),
        rubric=rubric or [],
    )


def _passages():
    return [
        RetrievedPassage(doc_id=uuid4(), doc_label="Goodfellow Ch.6", text="computes gradients via the chain rule"),
        RetrievedPassage(doc_id=uuid4(), doc_label="Goodfellow Ch.6", text="a separate optimizer updates the weights"),
    ]


def test_build_rubric_grounds_points_in_real_passages():
    passages = _passages()
    draft = _RubricDraft(points=[
        _RubricItem(criterion="computes gradients via chain rule", passage_index=0, quote="chain rule"),
        _RubricItem(criterion="a separate optimizer updates weights", passage_index=1, quote="separate optimizer"),
    ])
    judge = ClaudeJudge(client=_FakeClient(rubric_draft=draft))
    rubric = judge.build_rubric(concept=_concept(), passages=passages)
    assert len(rubric) == 2
    assert rubric[0].citation.doc_id == passages[0].doc_id  # identifiers from the real passage
    assert rubric[1].citation.doc_id == passages[1].doc_id


def test_rubric_prompt_respects_depth():
    draft = _RubricDraft(points=[_RubricItem(criterion="c", passage_index=0, quote="q")])

    client = _FakeClient(rubric_draft=draft)
    expert = _concept()
    expert.depth = "expert"
    ClaudeJudge(client=client).build_rubric(concept=expert, passages=_passages())
    assert "failure modes" in client.messages.calls[0]["system"]   # expert bar

    client2 = _FakeClient(rubric_draft=draft)
    overview = _concept()
    overview.depth = "overview"
    ClaudeJudge(client=client2).build_rubric(concept=overview, passages=_passages())
    assert "big picture" in client2.messages.calls[0]["system"]    # overview bar


def test_build_rubric_from_knowledge_when_no_source():
    # tier-3: no passages -> build from model knowledge, flagged lower-confidence
    from feynman_loop.models import MODEL_FALLBACK_LABEL

    draft = _RubricDraft(points=[
        _RubricItem(criterion="computes gradients via the chain rule", passage_index=0, quote="brief fact"),
    ])
    judge = ClaudeJudge(client=_FakeClient(rubric_draft=draft))
    rubric = judge.build_rubric(concept=_concept(), passages=[])
    assert rubric[0].citation.doc_label == MODEL_FALLBACK_LABEL
    assert rubric[0].citation.doc_id is None


_EXPLANATION = ("Backprop computes gradients via the chain rule, "
                "and a separate optimizer applies the weight update.")


def test_evaluate_score_is_computed_from_statuses():
    rubric = [
        RubricPoint(criterion="A", citation=Citation(doc_label="d", quote="qa")),
        RubricPoint(criterion="B", citation=Citation(doc_label="d", quote="qb")),
        RubricPoint(criterion="C", citation=Citation(doc_label="d", quote="qc")),
    ]
    score_draft = _ScoreDraft(scores=[
        _CriterionStatus(index=0, status="met", evidence="computes gradients via the chain rule", probe=""),
        _CriterionStatus(index=1, status="partial", evidence="a separate optimizer", probe="What about B?"),
        _CriterionStatus(index=2, status="missed", probe="What about C?"),
    ])
    judge = ClaudeJudge(client=_FakeClient(score_draft=score_draft))
    report = judge.evaluate(concept=_concept(rubric=rubric), user_explanation=_EXPLANATION)

    assert abs(report.understanding_level - (1.0 + 0.5 + 0.0) / 3) < 1e-9  # computed, not guessed
    assert report.correct_points == ["A"]                                 # only the "met" point
    assert [g.description for g in report.gaps] == ["What about B?", "What about C?"]  # probes, not answers
    assert report.gaps[0].citation.quote == "qb"                          # citation retained for audit
    assert report.evidence_failures == 0


def test_evaluate_downgrades_credit_without_evidence():
    # the judge proposes; the code verifies. "met" backed by a quote that is not in the learner's
    # text drops to partial; "partial" with no findable evidence drops to missed.
    rubric = [
        RubricPoint(criterion="chain rule computes the gradients", citation=Citation(doc_label="d", quote="qa")),
        RubricPoint(criterion="optimizer applies the update", citation=Citation(doc_label="d", quote="qb")),
    ]
    score_draft = _ScoreDraft(scores=[
        _CriterionStatus(index=0, status="met", evidence="this quote was never written by the learner", probe=""),
        _CriterionStatus(index=1, status="partial", evidence="also not in the text", probe="Who updates the weights?"),
    ])
    judge = ClaudeJudge(client=_FakeClient(score_draft=score_draft))
    report = judge.evaluate(concept=_concept(rubric=rubric), user_explanation=_EXPLANATION)

    assert abs(report.understanding_level - (0.5 + 0.0) / 2) < 1e-9
    assert report.correct_points == []
    assert report.evidence_failures == 2
    # the downgraded "met" still produces a probe, and it never names the criterion
    assert "chain rule" not in report.gaps[0].description
    assert "own words" in report.gaps[0].description


def test_evaluate_requires_a_rubric():
    judge = ClaudeJudge(client=_FakeClient(score_draft=_ScoreDraft(scores=[])))
    with pytest.raises(ValueError):
        judge.evaluate(concept=_concept(rubric=[]), user_explanation="x")
