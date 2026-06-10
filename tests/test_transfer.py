"""Transfer engine + orchestration tests. Fake client, offline, no tokens. Verifies the
grounding integrity (rubric identifiers come from real passages, by index) and the scoring math."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from feynman_loop.models import (
    Citation,
    Concept,
    RubricPoint,
    SourceRef,
    SourceTier,
    TransferProbe,
    TransferResult,
    UserState,
)
from feynman_loop.retrieval.base import RetrievedPassage
from feynman_loop.transfer.claude_transfer import (
    ClaudeTransfer,
    _CriterionScore,
    _ProbeDraft,
    _RubricItem,
    _ScoreDraft,
)


class _FakeMessages:
    def __init__(self, probe_draft=None, score_draft=None):
        self.probe_draft = probe_draft
        self.score_draft = score_draft

    def parse(self, **kw):
        if kw["output_format"].__name__ == "_ProbeDraft":
            return SimpleNamespace(parsed_output=self.probe_draft)
        return SimpleNamespace(parsed_output=self.score_draft)


class _FakeClient:
    def __init__(self, probe_draft=None, score_draft=None):
        self.messages = _FakeMessages(probe_draft, score_draft)


def _concept():
    return Concept(
        label="Backpropagation",
        source_ref=SourceRef(tier=SourceTier.UPLOADED, doc_id=uuid4(),
                             doc_label="Goodfellow Ch.6", retrieval_query="backprop"),
    )


def _passages():
    return [
        RetrievedPassage(doc_id=uuid4(), doc_label="Goodfellow Ch.6", text="chain rule recursively", score=0.9),
        RetrievedPassage(doc_id=uuid4(), doc_label="Goodfellow Ch.6", text="only computes gradients", score=0.8),
    ]


def test_generate_probe_grounds_rubric_in_real_passages():
    passages = _passages()
    draft = _ProbeDraft(
        question="Apply backprop to a 2-layer net you weren't shown.",
        rubric=[
            _RubricItem(criterion="uses the chain rule", passage_index=0, quote="chain rule recursively"),
            _RubricItem(criterion="notes it only computes gradients", passage_index=1, quote="only computes gradients"),
        ],
    )
    engine = ClaudeTransfer(client=_FakeClient(probe_draft=draft))
    probe = engine.generate_probe(concept=_concept(), passages=passages)

    assert len(probe.rubric) == 2
    # identifiers come from the real passages, by index, not the model
    assert probe.rubric[0].citation.doc_id == passages[0].doc_id
    assert probe.rubric[1].citation.doc_id == passages[1].doc_id


def test_generate_probe_clamps_bad_index():
    passages = _passages()
    draft = _ProbeDraft(question="q", rubric=[_RubricItem(criterion="c", passage_index=9, quote="x")])
    engine = ClaudeTransfer(client=_FakeClient(probe_draft=draft))
    probe = engine.generate_probe(concept=_concept(), passages=passages)
    assert probe.rubric[0].citation.doc_id == passages[0].doc_id  # clamped to a real passage


def test_generate_probe_from_knowledge_when_no_source():
    # tier-3: no passages -> generate the transfer task from model knowledge, flagged
    from feynman_loop.models import MODEL_FALLBACK_LABEL

    draft = _ProbeDraft(question="Apply it to a novel case you weren't shown.", rubric=[
        _RubricItem(criterion="uses the chain rule", passage_index=0, quote="brief fact"),
    ])
    engine = ClaudeTransfer(client=_FakeClient(probe_draft=draft))
    probe = engine.generate_probe(concept=_concept(), passages=[])
    assert probe.rubric[0].citation.doc_label == MODEL_FALLBACK_LABEL
    assert probe.rubric[0].citation.doc_id is None


def test_generate_probe_refuses_when_nothing_grounds():
    draft = _ProbeDraft(question="q", rubric=[])
    engine = ClaudeTransfer(client=_FakeClient(probe_draft=draft))
    with pytest.raises(ValueError):
        engine.generate_probe(concept=_concept(), passages=_passages())


def test_generate_remediation_grounds_in_real_passages():
    passages = _passages()
    draft = _ProbeDraft(
        question="Narrower: where does the chain rule first apply in a 2-layer net?",
        rubric=[_RubricItem(criterion="identifies the chain-rule step", passage_index=0, quote="chain rule recursively")],
    )
    engine = ClaudeTransfer(client=_FakeClient(probe_draft=draft))
    missed = [RubricPoint(criterion="uses the chain rule", citation=Citation(doc_label="d", quote="q"))]
    probe = engine.generate_remediation(concept=_concept(), passages=passages, missed=missed)
    assert probe.rubric[0].citation.doc_id == passages[0].doc_id  # grounded in the real passage


def test_score_answer_computes_fraction_and_split():
    cid = uuid4()
    probe = TransferProbe(
        concept_id=cid, question="q",
        rubric=[
            RubricPoint(criterion="A", citation=Citation(doc_label="d", quote="qa")),
            RubricPoint(criterion="B", citation=Citation(doc_label="d", quote="qb")),
            RubricPoint(criterion="C", citation=Citation(doc_label="d", quote="qc")),
        ],
    )
    score_draft = _ScoreDraft(scores=[
        _CriterionScore(index=0, met=True, note="ok"),
        _CriterionScore(index=1, met=False, note="no"),
        _CriterionScore(index=2, met=True, note="ok"),
    ])
    engine = ClaudeTransfer(client=_FakeClient(score_draft=score_draft))
    result = engine.score_answer(probe=probe, user_answer="...")

    assert abs(result.transfer_score - 2 / 3) < 1e-9
    assert result.met == ["A", "C"]
    assert [m.criterion for m in result.missed] == ["B"]


def test_score_transfer_records_separate_transfer_level(tmp_path):
    from feynman_loop.loop import score_transfer
    from feynman_loop.storage import JsonUserStateStore

    cid, uid = uuid4(), uuid4()
    store = JsonUserStateStore(tmp_path / "s.json")
    store.put(UserState(concept_id=cid, user_id=uid, understanding_level=0.7))

    probe = TransferProbe(concept_id=cid, question="q",
                          rubric=[RubricPoint(criterion="A", citation=Citation(doc_label="d", quote="q"))])

    class _FakeEngine:
        def generate_probe(self, **k):
            raise NotImplementedError

        def score_answer(self, *, probe, user_answer):
            return TransferResult(concept_id=cid, question="q", user_answer=user_answer,
                                  transfer_score=0.5, met=[], missed=[])

    from datetime import datetime, timezone

    from feynman_loop.scheduling import compute_next_due

    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    result = score_transfer(probe=probe, user_id=uid, user_answer="x", engine=_FakeEngine(), store=store, now=now)

    assert result.transfer_score == 0.5
    saved = store.get(user_id=uid, concept_id=cid)
    assert saved.transfer_level == 0.5
    assert saved.understanding_level == 0.7  # transfer did NOT clobber the explanation score
    assert saved.next_due_at == compute_next_due(0.5, now=now)  # weakest-link: min(0.7, 0.5) governs
