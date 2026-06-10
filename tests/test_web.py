"""Web API flow test. Overrides the _make_* factories with fakes so the full
session -> review -> transfer path runs offline, no tokens, no model download."""

import pytest
from fastapi.testclient import TestClient

from feynman_loop.models import (
    Citation,
    Gap,
    GapReport,
    RubricPoint,
    TransferProbe,
    TransferResult,
)
from feynman_loop.retrieval.base import RetrievedPassage
from feynman_loop.web import app as webapp


class _FakeRetriever:
    def ingest(self, *, doc_id, doc_label, text):
        self._doc_id, self._doc_label = doc_id, doc_label

    def retrieve(self, *, query, k=4):
        return [RetrievedPassage(doc_id=self._doc_id, doc_label=self._doc_label, text="chain rule recursively")]


class _FakeJudge:
    def evaluate(self, *, concept, user_explanation, passages):
        return GapReport(
            concept_id=concept.id,
            user_explanation=user_explanation,
            understanding_level=0.8,  # above TRANSFER_GATE -> transfer should be offered
            correct_points=["computes gradients"],
            gaps=[Gap(description="omits the optimizer step",
                      citation=Citation(doc_label=passages[0].doc_label, quote="separate optimizer"))],
        )


class _FakeTransfer:
    def generate_probe(self, *, concept, passages):
        return TransferProbe(
            concept_id=concept.id,
            question="Apply it to a 2-layer net you weren't shown.",
            rubric=[RubricPoint(criterion="uses chain rule",
                                citation=Citation(doc_label=passages[0].doc_label, quote="chain rule recursively"))],
        )

    def score_answer(self, *, probe, user_answer):
        return TransferResult(concept_id=probe.concept_id, question=probe.question,
                              user_answer=user_answer, transfer_score=1.0, met=["uses chain rule"], missed=[])


class _FakeExpander:
    def expand(self, *, concept_label):
        return f"{concept_label} expanded query"


@pytest.fixture
def client(monkeypatch, tmp_path):
    from feynman_loop.storage import JsonUserStateStore

    monkeypatch.setattr(webapp, "_make_retriever", lambda: _FakeRetriever())
    monkeypatch.setattr(webapp, "_make_judge", lambda: _FakeJudge())
    monkeypatch.setattr(webapp, "_make_transfer", lambda: _FakeTransfer())
    monkeypatch.setattr(webapp, "_make_expander", lambda: _FakeExpander())
    monkeypatch.setattr(webapp, "_make_store", lambda: JsonUserStateStore(tmp_path / "s.json"))
    webapp._SESSIONS.clear()
    return TestClient(webapp.app)


def test_full_flow_session_review_transfer(client):
    # 1. start a session
    r = client.post("/api/session", json={
        "source_text": "Backprop applies the chain rule recursively. A separate optimizer updates weights.",
        "concept_label": "Backpropagation",
    })
    assert r.status_code == 200
    sid = r.json()["session_id"]

    # 2. submit an explanation -> grounded gaps + a transfer question (gate passed at 0.8)
    r = client.post("/api/review", json={"session_id": sid, "explanation": "backprop computes gradients"})
    assert r.status_code == 200
    body = r.json()
    assert body["understanding_level"] == 0.8
    assert body["gaps"][0]["quote"] == "separate optimizer"
    assert body["transfer_question"]  # offered because >= gate
    assert body["review_count"] == 1

    # 3. answer the transfer challenge -> scored against the grounded rubric
    r = client.post("/api/transfer", json={"session_id": sid, "answer": "uses the chain rule layer by layer"})
    assert r.status_code == 200
    t = r.json()
    assert t["transfer_score"] == 1.0
    assert t["met"] == ["uses chain rule"]


def test_unknown_session_404(client):
    r = client.post("/api/review", json={"session_id": "nope", "explanation": "x"})
    assert r.status_code == 404
