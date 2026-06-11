"""Learner ledger tests: identity stability, concept persistence, event log, derived profile,
and the failure-mode tagger (fake client, offline)."""

from types import SimpleNamespace
from uuid import uuid4

from feynman_loop.learner import (
    ClaudeMissTagger,
    JsonLearnerLog,
    ReviewEvent,
    _TagDraft,
    derive_profile,
)
from feynman_loop.models import Citation, Concept, RubricPoint, SourceRef, SourceTier
from feynman_loop.storage import JsonConceptStore, JsonIdentity


def _concept(label="Backpropagation"):
    return Concept(
        label=label,
        source_ref=SourceRef(tier=SourceTier.MODEL_FALLBACK, doc_label="general knowledge (unverified)",
                             retrieval_query=label),
        rubric=[RubricPoint(criterion="x", citation=Citation(doc_label="d", quote="q"))],
    )


def test_identity_is_stable_across_instances(tmp_path):
    p = tmp_path / "user.json"
    first = JsonIdentity(p).user_id()
    second = JsonIdentity(p).user_id()  # a fresh instance = a fresh process
    assert first == second


def test_concept_store_roundtrip_and_label_lookup(tmp_path):
    store = JsonConceptStore(tmp_path / "concepts.json")
    c = _concept()
    store.put(c)
    reloaded = JsonConceptStore(tmp_path / "concepts.json")  # simulate restart
    assert reloaded.get(c.id).label == "Backpropagation"
    assert reloaded.find_by_label("backprop") is None  # a genuinely different label: no match
    assert reloaded.find_by_label("BACKPROPAGATION ").id == c.id  # case/space-insensitive match
    assert reloaded.get(c.id).rubric[0].criterion == "x"  # rubric survives


def test_learner_log_roundtrip(tmp_path):
    log = JsonLearnerLog(tmp_path / "log.json")
    log.append(ReviewEvent(concept_id=uuid4(), concept_label="Backprop", kind="explain",
                           score=0.5, missed=["m1"], tags=["mechanism"]))
    log.append(ReviewEvent(concept_id=uuid4(), concept_label="IPO", kind="transfer", score=0.2))
    events = JsonLearnerLog(tmp_path / "log.json").events()  # fresh instance = restart
    assert [e.kind for e in events] == ["explain", "transfer"]
    assert events[0].tags == ["mechanism"]


def test_derive_profile_surfaces_apply_gap_and_weak_mode():
    cid = uuid4()
    events = [
        ReviewEvent(concept_id=cid, concept_label="A", kind="explain", score=0.8, tags=["mechanism"]),
        ReviewEvent(concept_id=cid, concept_label="A", kind="transfer", score=0.3, tags=["application", "mechanism"]),
        ReviewEvent(concept_id=cid, concept_label="B", kind="explain", score=0.9, tags=["mechanism"]),
    ]
    p = derive_profile(events)
    assert p["reviews"] == 3 and p["concepts"] == 2
    assert p["avg_explain"] == 0.85 and p["avg_transfer"] == 0.3
    assert p["weak_modes"][0] == "mechanism"
    assert "apply" in p["insight"]  # the explain-vs-apply gap is called out


def test_derive_profile_empty():
    assert derive_profile([])["reviews"] == 0


def test_tagger_pairs_tags_by_index():
    class _Msgs:
        def parse(self, **kw):
            return SimpleNamespace(parsed_output=_TagDraft(tags=["mechanism", "application", "context"]))

    tagger = ClaudeMissTagger(client=SimpleNamespace(messages=_Msgs()))
    assert tagger.tag(["a", "b"]) == ["mechanism", "application"]  # extras dropped
    assert tagger.tag([]) == []  # no call, no tags


def test_streak_counts_consecutive_days_and_forgives_today():
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from feynman_loop.learner import streak_days

    now = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)

    def ev(days_ago):
        return ReviewEvent(concept_id=uuid4(), concept_label="X", kind="explain", score=0.5,
                           at=now - timedelta(days=days_ago))

    assert streak_days([], now=now) == 0
    assert streak_days([ev(0)], now=now) == 1
    assert streak_days([ev(0), ev(1), ev(2)], now=now) == 3
    assert streak_days([ev(1)], now=now) == 1   # today's rep not done yet: streak alive, not grown
    assert streak_days([ev(2)], now=now) == 0   # a full missed day kills it
    assert streak_days([ev(0), ev(2)], now=now) == 1
