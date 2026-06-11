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

    def evaluate_point(self, *, criterion, question, answer):
        # deterministic single-point verdict for volley tests
        if "right" in answer:
            return "met", True, ""
        return "missed", True, f"Try again: what about {criterion}?"

    def make_point_questions(self, *, concept_label, criteria):
        return [f"What is {c}?" for c in criteria]


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
    # WHY pinned True: these tests exercise the independent-judge path; without the pin the mode
    # would silently follow whether the developer's shell happens to export ANTHROPIC_API_KEY.
    monkeypatch.setattr(srv, "_independent_judge_available", lambda: True)
    monkeypatch.setattr(srv, "_make_retriever", lambda: _FakeRetriever())
    monkeypatch.setattr(srv, "_make_judge", lambda: _FakeJudge())
    monkeypatch.setattr(srv, "_make_transfer", lambda: _FakeTransfer())
    monkeypatch.setattr(srv, "_make_expander", lambda: _FakeExpander())
    monkeypatch.setattr(srv, "_make_related", lambda: _FakeRelated())
    monkeypatch.setattr(srv, "_make_tagger", lambda: _FakeTagger())
    srv._CHECKS.clear()


@pytest.fixture()
def zero_key(monkeypatch):
    """No API key: the host judges under the verified protocol. The API factories are booby-trapped
    so any zero-key path that quietly reaches for the API fails the test loudly."""
    monkeypatch.setattr(srv, "_independent_judge_available", lambda: False)

    def _boom():
        raise AssertionError("API factory must not be used in zero-key mode")

    monkeypatch.setattr(srv, "_make_judge", _boom)
    monkeypatch.setattr(srv, "_make_transfer", _boom)
    monkeypatch.setattr(srv, "_make_expander", _boom)
    monkeypatch.setattr(srv, "_make_related", _boom)


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


def test_depth_persists_and_change_rebuilds():
    a = srv.start_check("Backpropagation", depth="expert")
    assert a["depth"] == "expert"
    srv._CHECKS.clear()  # restart

    b = srv.start_check("backpropagation")  # no depth given -> keeps the stored expert bar
    assert b["depth"] == "expert" and b["returning_concept"] is True

    c = srv.start_check("Backpropagation", depth="overview")  # explicit change -> rebuild
    assert c["depth"] == "overview"
    assert srv.start_check("Backpropagation", depth="bogus")["depth"] == "overview"  # invalid -> kept


def test_concept_label_whitespace_is_normalized():
    a = srv.start_check("  Backpropagation \n ")
    assert a["concept"] == "Backpropagation"
    b = srv.start_check("Backpropagation")
    assert b["returning_concept"] is True  # one concept, not a whitespace fork


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


# --- zero-key mode: the host model judges; the server verifies and computes ---

_ZK_EXPLANATION = ("backprop computes gradients of the loss with the chain rule, recursively, "
                   "so every parameter gets a gradient")


def _zk_rubric_points():
    # three quotes the (fake) passage really contains, one invented
    return [
        {"criterion": "uses the chain rule", "passage_index": 0, "quote": "chain rule recursively"},
        {"criterion": "applies it layer by layer", "passage_index": 0, "quote": "chain rule recursively"},
        {"criterion": "gradients reach every parameter", "passage_index": 0, "quote": "this text is in no passage"},
        {"criterion": "computes gradients of the loss", "passage_index": 0, "quote": "chain rule recursively"},
    ]


def test_zero_key_full_flow(zero_key):
    started = srv.start_check("Backpropagation", source_text="backprop applies the chain rule recursively.")
    assert started["action"] == "build_rubric"
    assert started["grounded"] is True
    assert started["passages"]                       # the host sees what to ground the rubric in
    cid = started["check_id"]

    # too few points for the depth -> rejected, phase preserved, host resubmits
    too_few = srv.submit_rubric(cid, points=_zk_rubric_points()[:2])
    assert "error" in too_few

    ok = srv.submit_rubric(cid, points=_zk_rubric_points(),
                           related=["Chain Rule", "Chain Rule", "Gradient Descent"])
    assert ok["rubric_points"] == 4
    assert ok["grounded_points"] == 3                # the invented quote was flagged, not trusted

    protocol = srv.judge_explanation(cid, _ZK_EXPLANATION)
    assert protocol["action"] == "judge_in_host"
    assert len(protocol["rubric"]) == 4
    assert "VERBATIM" in protocol["instruction"]

    judged = srv.submit_judgment(cid, [
        {"index": 0, "status": "met", "evidence": "with the chain rule"},
        {"index": 1, "status": "met", "evidence": "recursively, so every parameter"},
        {"index": 2, "status": "met", "evidence": "this evidence is fabricated entirely"},  # -> partial
        {"index": 3, "status": "met", "evidence": "computes gradients of the loss"},
    ])
    assert judged["judge"] == "host-verified"
    assert judged["evidence_failures"] == 1
    assert judged["understanding_level"] == round((1 + 1 + 0.5 + 1) / 4, 2)
    assert judged["transfer_available"] is True
    assert len(judged["gaps"]) == 1                  # only the downgraded point

    # transfer: host creates the probe, server verifies its grounding, then judges the answer
    q = srv.make_transfer(cid)
    assert q["action"] == "make_transfer_in_host"
    probe = srv.submit_transfer_probe(
        cid, question="Apply backprop to a two-layer net: which gradient is computed first?",
        points=[{"criterion": "starts from the output layer", "passage_index": 0,
                 "quote": "chain rule recursively"}])
    assert "which gradient" in probe["question"]

    ans = srv.score_transfer(cid, "you start from the output layer and apply the chain rule backwards")
    assert ans["action"] == "judge_transfer_in_host"
    scored = srv.submit_judgment(cid, [
        {"index": 0, "status": "met", "evidence": "start from the output layer"}])
    assert scored["transfer_score"] == 1.0
    assert scored["judge"] == "host-verified"
    assert "First transfer passed" in scored["milestones"]
    assert scored["streak_days"] >= 1

    # the ledger is identical in kind to API mode, with provenance recorded
    srv._CHECKS.clear()                              # restart
    prog = srv.progress()
    assert [c["concept"] for c in prog["concepts"]] == ["Backpropagation"]
    assert prog["concepts"][0]["transfer_level"] == 1.0
    events = srv._make_learner_log().events()
    assert [e.judge for e in events] == ["host", "host"]
    assert prog["learner"]["reviews"] == 2


def test_zero_key_incomplete_verdicts_cannot_inflate(zero_key):
    started = srv.start_check("Backpropagation", source_text="backprop applies the chain rule recursively.")
    cid = started["check_id"]
    srv.submit_rubric(cid, points=_zk_rubric_points())
    srv.judge_explanation(cid, _ZK_EXPLANATION)
    # host submits a single verdict: the other three count as missed, never as met
    judged = srv.submit_judgment(cid, [{"index": 0, "status": "met", "evidence": "with the chain rule"}])
    assert judged["understanding_level"] == 0.25


def test_zero_key_phase_is_enforced(zero_key):
    started = srv.start_check("Osmosis")               # tier-3, rubric pending
    cid = started["check_id"]
    assert started["grounded"] is False
    assert "error" in srv.submit_judgment(cid, [])     # nothing locked in yet
    assert "error" in srv.submit_transfer_probe(cid, "a question long enough to pass", points=[])
    assert "error" in srv.judge_explanation(cid, "x")  # rubric not built yet
    assert "error" in srv.make_transfer(cid)           # transfer not unlocked


def test_zero_key_knowledge_rubric_and_remediation(zero_key):
    started = srv.start_check("Entropy")
    cid = started["check_id"]
    pts = [{"criterion": f"distinct checkable idea number {i}", "passage_index": 0,
            "quote": "a brief supporting fact"} for i in range(4)]
    ok = srv.submit_rubric(cid, points=pts, related=["Information Theory"])
    assert ok["grounded_points"] is None               # nothing to verify against; flagged tier
    srv.judge_explanation(cid, "entropy measures average surprise of a distribution")
    judged = srv.submit_judgment(cid, [
        {"index": i, "status": "met", "evidence": "measures average surprise"} for i in range(4)])
    assert judged["transfer_available"] is True

    srv.make_transfer(cid)
    srv.submit_transfer_probe(cid, question="A coin is biased 90/10: what happens to entropy and why?",
                              points=[{"criterion": "entropy falls as the distribution sharpens",
                                       "passage_index": 0, "quote": "supporting fact"}])
    srv.score_transfer(cid, "no idea at all honestly")
    failed = srv.submit_judgment(cid, [{"index": 0, "status": "missed", "evidence": "", "probe": ""}])
    assert failed["transfer_score"] == 0.0
    assert failed["action"] == "make_remediation"      # ONE bounded retry, host-generated

    retry = srv.submit_transfer_probe(cid, question="What does sharpening a distribution do to surprise?",
                                      points=[{"criterion": "less surprise on average",
                                               "passage_index": 0, "quote": "fact"}])
    assert "surprise" in retry["question"]
    srv.score_transfer(cid, "less surprise on average, so entropy goes down")
    second = srv.submit_judgment(cid, [
        {"index": 0, "status": "met", "evidence": "less surprise on average"}])
    assert "action" not in second                      # no second remediation: bounded


def test_zero_key_returning_concept_skips_rubric_build(zero_key):
    started = srv.start_check("Backpropagation", source_text="backprop applies the chain rule recursively.")
    srv.submit_rubric(started["check_id"], points=_zk_rubric_points())
    srv._CHECKS.clear()                                # restart

    again = srv.start_check("backpropagation")
    assert again["returning_concept"] is True
    assert "action" not in again                       # stored rubric reused; start is instant


def test_submit_tools_error_in_api_mode():
    started = srv.start_check("Backpropagation")       # API mode: rubric built by the judge
    assert "error" in srv.submit_rubric(started["check_id"], points=_zk_rubric_points())
    assert "error" in srv.submit_judgment(started["check_id"], [])


# --- rapid mode: the 2-minute volley ---

def test_rapid_volley_independent(monkeypatch):
    class _TwoPointJudge(_FakeJudge):
        def build_rubric(self, *, concept, passages):
            label = passages[0].doc_label if passages else "general knowledge (unverified)"
            return [
                RubricPoint(criterion="first idea", citation=Citation(doc_label=label, quote="q1"),
                            question="Q1?"),
                RubricPoint(criterion="second idea", citation=Citation(doc_label=label, quote="q2"),
                            question="Q2?"),
            ]

    monkeypatch.setattr(srv, "_make_judge", lambda: _TwoPointJudge())
    started = srv.quick_check("Osmosis")
    assert started["mode"] == "rapid"
    assert started["total_questions"] == 2
    assert started["question"] == "Q1?"
    cid = started["check_id"]

    mid = srv.answer(cid, "the right idea in one line")
    assert mid["verdict"] == "met"
    assert mid["progress"] == "1/2"
    assert mid["next_question"] == "Q2?"

    done = srv.answer(cid, "no clue")
    assert done["done"] is True
    assert done["understanding_level"] == 0.5          # (1 + 0)/2, computed in code, not vibes
    assert done["streak_days"] >= 1                    # today's rep counts toward the streak
    assert "First check complete" in done["milestones"]  # the rep that unlocked it shows it
    assert done["level"].startswith("untested -> ")      # territory gained, visibly
    assert "Try again" in done["gaps"][0]["probe"]     # a probe, never the answer
    assert srv._make_learner_log().events()[-1].mode == "rapid"


def test_rapid_volley_questions_backfilled_for_legacy_rubrics():
    started = srv.quick_check("Backpropagation")       # _FakeJudge rubric has no question field
    assert started["question"] == "What is x?"         # backfilled via make_point_questions
    assert srv._make_concept_store().find_by_label("Backpropagation").rubric[0].question == "What is x?"


def test_rapid_volley_zero_key(zero_key):
    first = srv.quick_check("Entropy")
    assert first["action"] == "build_rubric"
    assert "quick_check again" in first["instruction"]
    srv.submit_rubric(first["check_id"], points=[
        {"criterion": f"distinct checkable idea number {i}", "passage_index": 0,
         "quote": "a brief supporting fact"} for i in range(4)])

    started = srv.quick_check("Entropy")
    assert started["total_questions"] == 4
    assert started["points"][0]["criterion"].startswith("distinct")
    cid = started["check_id"]

    for i in range(3):
        r = srv.answer(cid, f"a real one liner answer number {i}",
                       status="met", evidence=f"one liner answer number {i}")
        assert r.get("verdict") == "met"
    done = srv.answer(cid, "another short answer", status="met", evidence="completely invented quote")
    assert done["done"] is True
    assert done["judge"] == "host-verified"
    assert done["evidence_failures"] == 1              # fabricated credit caught and downgraded
    assert done["understanding_level"] == round((1 + 1 + 1 + 0.5) / 4, 2)


def test_answer_requires_a_running_volley():
    started = srv.start_check("Osmosis")               # full mode, no volley
    assert "error" in srv.answer(started["check_id"], "x")


# --- grounding: direct passages, the stored snapshot, and restart survival ---

def test_split_passages_caps_blocks_and_covers_the_source():
    from uuid import uuid4

    text = "\n\n".join(f"paragraph {i} " + "x" * 400 for i in range(20))
    ps = srv._split_passages(uuid4(), "doc", text)
    assert 1 <= len(ps) <= 6                            # bounded prompt, no retrieval sampling
    joined = "\n\n".join(p.text for p in ps)
    assert "paragraph 0" in joined and "paragraph 19" in joined  # nothing dropped
    assert all(p.doc_label == "doc" for p in ps)


def test_long_source_goes_through_the_retriever():
    s = srv.start_check("Backpropagation", source_text="backprop section. " * 800)  # > direct limit
    assert s["grounded"] is True                        # FakeRetriever path produced the rubric


def test_depth_change_stays_grounded_with_snapshot():
    a = srv.start_check("Backpropagation", source_text="backprop applies the chain rule recursively.")
    assert a["grounded"] is True
    srv._CHECKS.clear()                                 # restart

    b = srv.start_check("Backpropagation", depth="expert")  # depth change, no new source
    assert b["depth"] == "expert"
    assert b["grounded"] is True                        # snapshot re-grounded it (was tier-3 before)


def test_zero_key_grounding_survives_restart(zero_key):
    s = srv.start_check("Backpropagation", source_text="backprop applies the chain rule recursively.")
    cid = s["check_id"]
    srv.submit_rubric(cid, points=_zk_rubric_points())
    srv.judge_explanation(cid, _ZK_EXPLANATION)
    srv.submit_judgment(cid, [
        {"index": 0, "status": "met", "evidence": "with the chain rule"},
        {"index": 1, "status": "met", "evidence": "recursively, so every parameter"},
        {"index": 2, "status": "met", "evidence": "every parameter gets a gradient"},
        {"index": 3, "status": "met", "evidence": "computes gradients of the loss"},
    ])
    srv._CHECKS.clear()                                 # restart: in-memory state gone

    again = srv.start_check("backpropagation")          # reuse: instant, rubric from the ledger
    assert "action" not in again
    cid2 = again["check_id"]
    retell = ("the loss gradient reaches every parameter because backprop applies the chain "
              "rule recursively, and then the optimizer updates the weights")
    srv.judge_explanation(cid2, retell)
    judged = srv.submit_judgment(cid2, [
        {"index": 0, "status": "met", "evidence": "applies the chain rule recursively"},
        {"index": 1, "status": "met", "evidence": "recursively"},
        {"index": 2, "status": "met", "evidence": "reaches every parameter"},
        {"index": 3, "status": "met", "evidence": "the loss gradient"},
    ])
    assert judged["transfer_available"] is True

    q = srv.make_transfer(cid2)
    assert q["action"] == "make_transfer_in_host"
    assert q["passages"]                                # grounding rebuilt from the stored snapshot
    assert "chain rule" in q["passages"][0]["text"]
