"""MCP tool tests. Fakes injected via the _make_* factories so the 5 tools run offline, no tokens.
Exercises the full flow (start -> judge -> make_transfer -> score_transfer -> progress), tier-3,
and error paths."""

import pytest

from feynman_loop import mcp_server as srv
from feynman_loop.models import (
    Citation,
    Gap,
    GapReport,
    RubricPoint,
    TransferProbe,
    TransferResult,
)
from feynman_loop.retrieval.base import RetrievedPassage
from feynman_loop.storage import JsonUserStateStore


class _FakeRetriever:
    def ingest(self, *, doc_id, doc_label, text):
        self._doc_id, self._doc_label = doc_id, doc_label

    def retrieve(self, *, query, k=4):
        return [RetrievedPassage(doc_id=self._doc_id, doc_label=self._doc_label, text="chain rule recursively")]


class _FakeJudge:
    def build_rubric(self, *, concept, passages):
        label = passages[0].doc_label if passages else "general knowledge (unverified)"
        return [RubricPoint(criterion="x", citation=Citation(doc_label=label, quote="q"))]

    def evaluate(self, *, concept, user_explanation):
        return GapReport(
            concept_id=concept.id, user_explanation=user_explanation, understanding_level=0.8,
            correct_points=["computes gradients"],
            gaps=[Gap(description="What performs the weight update?", citation=Citation(doc_label="src", quote="optimizer"))],
        )


class _FakeTransfer:
    def generate_probe(self, *, concept, passages):
        return TransferProbe(concept_id=concept.id, question="Apply it to a 2-layer net.",
                             rubric=[RubricPoint(criterion="chain rule", citation=Citation(doc_label="src", quote="q"))])

    def generate_remediation(self, *, concept, passages, missed):
        return TransferProbe(concept_id=concept.id, question="Narrower retry.",
                             rubric=[RubricPoint(criterion="chain rule", citation=Citation(doc_label="src", quote="q"))])

    def score_answer(self, *, probe, user_answer):
        return TransferResult(concept_id=probe.concept_id, question=probe.question, user_answer=user_answer,
                              transfer_score=1.0, met=["chain rule"], missed=[])


class _FakeExpander:
    def expand(self, *, concept_label):
        return f"{concept_label} expanded"


@pytest.fixture(autouse=True)
def fakes(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "_make_retriever", lambda: _FakeRetriever())
    monkeypatch.setattr(srv, "_make_judge", lambda: _FakeJudge())
    monkeypatch.setattr(srv, "_make_transfer", lambda: _FakeTransfer())
    monkeypatch.setattr(srv, "_make_expander", lambda: _FakeExpander())
    monkeypatch.setattr(srv, "_make_store", lambda: JsonUserStateStore(tmp_path / "s.json"))
    srv._CHECKS.clear()


def test_full_mcp_flow_and_progress():
    started = srv.start_check("Backpropagation", source_text="backprop applies the chain rule.")
    assert started["grounded"] is True
    cid = started["check_id"]

    judged = srv.judge_explanation(cid, "backprop computes gradients")
    assert judged["understanding_level"] == 0.8
    assert judged["gaps"][0]["probe"]                 # a probe, not the answer
    assert "do not answer" in judged["instruction"].lower()
    assert judged["transfer_available"] is True

    q = srv.make_transfer(cid)
    assert q["question"]

    scored = srv.score_transfer(cid, "uses the chain rule layer by layer")
    assert scored["transfer_score"] == 1.0
    assert scored["met"] == ["chain rule"]
    assert scored["remediation_question"] is None     # passed, no retry

    prog = srv.progress()
    labels = [c["concept"] for c in prog["concepts"]]
    assert "Backpropagation" in labels


def test_tier3_when_no_source():
    started = srv.start_check("Entropy")  # no source_text -> general knowledge
    assert started["grounded"] is False
    judged = srv.judge_explanation(started["check_id"], "it's disorder")
    assert judged["grounded"] is False


def test_unknown_check_id_is_handled():
    assert "error" in srv.judge_explanation("nope", "x")
    assert "error" in srv.make_transfer("nope")
    assert "error" in srv.score_transfer("nope", "x")


def test_make_transfer_requires_unlock():
    # a low-understanding review shouldn't unlock transfer; force it by not judging first
    started = srv.start_check("Osmosis")
    assert "error" in srv.make_transfer(started["check_id"])  # transfer_available is False until judged
