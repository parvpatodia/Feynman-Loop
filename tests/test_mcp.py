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


class _FakeTagger:
    def tag(self, missed):
        return ["mechanism" for _ in missed]


class _FakeRelated:
    def related_to(self, concept_label):
        return ["Chain Rule"]


@pytest.fixture(autouse=True)
def fakes(monkeypatch, tmp_path):
    # WHY only _ROOT is patched for storage: the default factories route through db.stores_for,
    # so pointing _ROOT at tmp gives the real SQLite ledger, exercised exactly as in production.
    monkeypatch.setattr(srv, "_ROOT", tmp_path)
    monkeypatch.delenv("FEYNMAN_VAULT", raising=False)
    monkeypatch.setattr(srv, "_make_retriever", lambda: _FakeRetriever())
    monkeypatch.setattr(srv, "_make_judge", lambda: _FakeJudge())
    monkeypatch.setattr(srv, "_make_transfer", lambda: _FakeTransfer())
    monkeypatch.setattr(srv, "_make_expander", lambda: _FakeExpander())
    monkeypatch.setattr(srv, "_make_related", lambda: _FakeRelated())
    monkeypatch.setattr(srv, "_make_tagger", lambda: _FakeTagger())
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


def test_memory_survives_a_server_restart():
    # THE moat property: the ledger must outlive the process. This is the test that was missing
    # when _USER_ID was a per-process uuid and progress() read only in-memory state.
    started = srv.start_check("Backpropagation")
    srv.judge_explanation(started["check_id"], "it computes gradients")

    srv._CHECKS.clear()  # simulate Claude Desktop restarting the server

    prog = srv.progress()
    assert [c["concept"] for c in prog["concepts"]] == ["Backpropagation"]  # read from disk
    assert prog["learner"]["reviews"] == 1                                   # ledger intact


def test_returning_concept_reuses_id_and_history():
    a = srv.start_check("Backpropagation")
    srv.judge_explanation(a["check_id"], "first attempt")
    srv._CHECKS.clear()  # restart

    b = srv.start_check("backpropagation ")  # same concept, sloppier label, no source
    assert b["returning_concept"] is True
    cid_a = srv._CHECKS and list(srv._CHECKS.values())[0].concept.id
    srv.judge_explanation(b["check_id"], "second attempt")

    prog = srv.progress()
    assert len(prog["concepts"]) == 1          # ONE concept, not a fork
    assert prog["learner"]["reviews"] == 2     # both attempts on its history
    assert cid_a is not None


def test_knowledge_map_renders_earned_nodes_and_frontier(tmp_path):
    started = srv.start_check("Backpropagation")
    srv.judge_explanation(started["check_id"], "it computes gradients via the chain rule")
    out = srv.knowledge_map()
    assert "graph TD" in out["mermaid"]
    assert "Backpropagation" in out["mermaid"]      # earned node, with status
    assert "Chain Rule" in out["mermaid"]           # frontier node from relations
    assert "---" in out["mermaid"]                  # an edge between them
    # the vault was synced too: markdown with wikilinks exists on disk
    assert (tmp_path / "vault" / "Backpropagation.md").exists()
    assert "[[Chain Rule]]" in (tmp_path / "vault" / "Backpropagation.md").read_text()
    assert (tmp_path / "vault" / "Feynman Knowledge Map.md").exists()


def test_knowledge_map_empty_ledger():
    out = srv.knowledge_map()
    assert "note" in out


def test_journey_shows_attempts_in_users_own_words():
    started = srv.start_check("Backpropagation")
    srv.judge_explanation(started["check_id"], "it sends errors backward to compute gradients")
    srv.judge_explanation(started["check_id"],
                          "applying the chain rule backward through the graph, then SGD updates weights")
    j = srv.journey("backpropagation")  # case-insensitive
    assert len(j["attempts"]) == 2
    assert "errors backward" in j["attempts"][0]["their_words"]
    assert j["headline"]  # first vs latest score


def test_rehearsed_attempt_gets_re_expression_ask_not_gaps():
    started = srv.start_check("Backpropagation")
    text = "backprop computes gradients of the loss via the chain rule and the optimizer updates weights"
    srv.judge_explanation(started["check_id"], text)
    second = srv.judge_explanation(started["check_id"], text)  # verbatim repeat
    assert second["rehearsed"] is True
    assert "gaps" not in second                      # the same prompts are not handed back
    assert "RE-EXPRESS" in second["instruction"]
    assert "never answer" in second["instruction"]


def test_progress_includes_learner_profile_and_due_list():
    started = srv.start_check("Backpropagation")
    srv.judge_explanation(started["check_id"], "explained")
    q = srv.make_transfer(started["check_id"])
    assert q["question"]
    srv.score_transfer(started["check_id"], "applied")

    prog = srv.progress()
    assert prog["learner"]["reviews"] == 2     # one explain + one transfer event
    assert "insight" in prog["learner"]
    assert "due_now" in prog
