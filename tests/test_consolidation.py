"""Tests for the journey mechanics: the echo fix (gated interval growth), near-verbatim
rehearsal detection, and their integration in run_review."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from feynman_loop.loop import is_near_verbatim, run_review
from feynman_loop.models import (
    Citation,
    Concept,
    Gap,
    GapReport,
    SourceRef,
    SourceTier,
    UserState,
)
from feynman_loop.scheduling import gated_next_due
from feynman_loop.storage import JsonUserStateStore

_NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _days(td):
    return td.total_seconds() / 86400


# --- gated_next_due: growth is earned by elapsed time ---

def test_early_review_growth_is_proportional():
    # prior interval 10d, reviewed only 1d in, perfect score (candidate 30d)
    last = _NOW - timedelta(days=1)
    due = gated_next_due(1.0, now=_NOW, prior_last_reviewed=last, prior_next_due=last + timedelta(days=10))
    # earned 10% of the growth: 10 + (30-10)*0.1 = 12 days, NOT 30
    assert abs(_days(due - _NOW) - 12.0) < 0.01


def test_on_time_review_earns_full_growth():
    last = _NOW - timedelta(days=10)
    due = gated_next_due(1.0, now=_NOW, prior_last_reviewed=last, prior_next_due=last + timedelta(days=10))
    assert abs(_days(due - _NOW) - 30.0) < 0.01


def test_shrink_applies_immediately_even_when_early():
    # failed 1 day in: a failure is real evidence regardless of timing
    last = _NOW - timedelta(days=1)
    due = gated_next_due(0.0, now=_NOW, prior_last_reviewed=last, prior_next_due=last + timedelta(days=10))
    assert abs(_days(due - _NOW) - 1.0) < 0.01


def test_rehearsed_attempt_never_grows_the_interval():
    last = _NOW - timedelta(days=9)  # almost on time AND perfect score, but rehearsed
    due = gated_next_due(1.0, now=_NOW, rehearsed=True,
                         prior_last_reviewed=last, prior_next_due=last + timedelta(days=10))
    assert abs(_days(due - _NOW) - 10.0) < 0.01  # holds the line, no growth


def test_first_review_gets_full_candidate():
    due = gated_next_due(1.0, now=_NOW, prior_last_reviewed=None, prior_next_due=None)
    assert abs(_days(due - _NOW) - 30.0) < 0.01


# --- near-verbatim detection: copies flagged, paraphrases welcomed ---

def test_verbatim_and_trivial_edits_are_flagged():
    a = "Backprop computes gradients of the loss via the chain rule; the optimizer updates weights."
    assert is_near_verbatim(a, a)
    assert is_near_verbatim(a, a.upper().replace(";", ","))


def test_paraphrase_is_not_flagged():
    a = "Backprop computes gradients of the loss via the chain rule; the optimizer updates weights."
    b = ("Working backward through the network, we apply calculus to find how much each weight "
         "contributed to the error, then SGD nudges the weights using those values.")
    assert not is_near_verbatim(a, b)


def test_empty_or_missing_prior_is_not_flagged():
    assert not is_near_verbatim("something", None)
    assert not is_near_verbatim("", "")


# --- integration: run_review detects rehearsal and gates the schedule ---

def _concept():
    return Concept(label="Backpropagation",
                   source_ref=SourceRef(tier=SourceTier.MODEL_FALLBACK,
                                        doc_label="general knowledge (unverified)", retrieval_query="x"))


class _FakeJudge:
    def __init__(self, level):
        self._level = level

    def build_rubric(self, *, concept, passages):
        return []

    def evaluate(self, *, concept, user_explanation):
        return GapReport(concept_id=concept.id, user_explanation=user_explanation,
                         understanding_level=self._level, correct_points=[],
                         gaps=[Gap(description="?", citation=Citation(doc_label="d", quote="q"))])


def test_run_review_flags_rehearsal_and_holds_interval(tmp_path):
    c, uid = _concept(), uuid4()
    store = JsonUserStateStore(tmp_path / "s.json")
    text = "Backprop computes gradients via the chain rule and the optimizer updates the weights."
    store.put(UserState(concept_id=c.id, user_id=uid, last_explanation=text,
                        understanding_level=0.5, last_reviewed_at=_NOW - timedelta(hours=1),
                        next_due_at=_NOW - timedelta(hours=1) + timedelta(days=10)))

    _, state, rehearsed = run_review(concept=c, user_id=uid, explanation=text,
                                     judge=_FakeJudge(0.95), store=store, now=_NOW)
    assert rehearsed is True
    assert abs(_days(state.next_due_at - _NOW) - 10.0) < 0.01  # held, not grown to ~28d
