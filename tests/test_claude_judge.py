"""ClaudeJudge tests. A fake client stands in for Anthropic so these run offline and spend
no tokens. They verify the wiring and the two grounding-integrity guarantees: identifiers come
from the passages (not the model), and an ungrounded concept is refused."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from feynman_loop.judge.claude_judge import ClaudeJudge, _GapVerdict, _JudgeVerdict
from feynman_loop.models import Concept, SourceRef, SourceTier
from feynman_loop.retrieval.base import RetrievedPassage


class _FakeMessages:
    def __init__(self, verdict):
        self._verdict = verdict
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed_output=self._verdict)


class _FakeClient:
    def __init__(self, verdict):
        self.messages = _FakeMessages(verdict)


def _concept():
    return Concept(
        label="Backpropagation",
        source_ref=SourceRef(
            tier=SourceTier.UPLOADED,
            doc_id=uuid4(),
            doc_label="Goodfellow Ch.6",
            retrieval_query="backprop",
        ),
    )


def test_evaluate_builds_a_grounded_report():
    real_doc_id = uuid4()
    passages = [
        RetrievedPassage(
            doc_id=real_doc_id,
            doc_label="Goodfellow Ch.6",
            text="Backpropagation applies the chain rule recursively over the computational graph.",
            score=0.9,
        )
    ]
    verdict = _JudgeVerdict(
        understanding_level=0.5,
        correct_points=["Correctly says gradients flow backward."],
        gaps=[_GapVerdict(description="Missed the chain rule.", passage_index=0, quote="chain rule recursively")],
    )
    judge = ClaudeJudge(client=_FakeClient(verdict))
    c = _concept()

    report = judge.evaluate(
        concept=c,
        user_explanation="Backprop just updates the weights.",
        passages=passages,
    )

    assert report.concept_id == c.id                      # injected by code, not the model
    assert report.user_explanation == "Backprop just updates the weights."
    assert report.understanding_level == 0.5
    assert len(report.gaps) == 1
    # the citation identifiers come from the passage, NOT the model
    assert report.gaps[0].citation.doc_id == real_doc_id
    assert report.gaps[0].citation.doc_label == "Goodfellow Ch.6"
    assert report.gaps[0].citation.quote == "chain rule recursively"
    # correct model id was sent
    assert judge._client.messages.calls[0]["model"] == "claude-opus-4-8"


def test_bad_passage_index_is_clamped_not_trusted():
    real_doc_id = uuid4()
    passages = [RetrievedPassage(doc_id=real_doc_id, doc_label="Doc A", text="...", score=0.1)]
    verdict = _JudgeVerdict(
        understanding_level=0.2,
        correct_points=[],
        gaps=[_GapVerdict(description="x", passage_index=7, quote="...")],  # out of range
    )
    judge = ClaudeJudge(client=_FakeClient(verdict))
    report = judge.evaluate(concept=_concept(), user_explanation="x", passages=passages)
    assert report.gaps[0].citation.doc_id == real_doc_id  # clamped to the real passage


def test_refuses_to_judge_without_passages():
    judge = ClaudeJudge(client=_FakeClient(None))
    with pytest.raises(ValueError):
        judge.evaluate(concept=_concept(), user_explanation="x", passages=[])
