"""SQLite ledger tests: behavior parity with the JSON stores, the JSON migration, and the
property that motivated the switch (no lost updates across concurrent store instances)."""

from datetime import datetime, timezone
from uuid import uuid4

from feynman_loop.db import SqliteConceptStore, SqliteLearnerLog, SqliteUserStateStore, stores_for
from feynman_loop.learner import JsonLearnerLog, ReviewEvent
from feynman_loop.models import Concept, SourceRef, SourceTier, UserState
from feynman_loop.storage import JsonConceptStore, JsonUserStateStore


def _concept(label="Backpropagation"):
    return Concept(label=label, related=["Chain Rule"],
                   source_ref=SourceRef(tier=SourceTier.MODEL_FALLBACK,
                                        doc_label="general knowledge (unverified)", retrieval_query=label))


def test_user_state_roundtrip_and_upsert(tmp_path):
    db = tmp_path / "feynman.db"
    store = SqliteUserStateStore(db)
    uid, cid = uuid4(), uuid4()
    store.put(UserState(concept_id=cid, user_id=uid, understanding_level=0.5))
    store.put(UserState(concept_id=cid, user_id=uid, understanding_level=0.8))  # upsert
    fresh = SqliteUserStateStore(db)  # new instance = new process
    assert fresh.get(user_id=uid, concept_id=cid).understanding_level == 0.8
    assert fresh.get(user_id=uid, concept_id=uuid4()) is None


def test_concept_roundtrip_and_label_lookup(tmp_path):
    db = tmp_path / "feynman.db"
    store = SqliteConceptStore(db)
    c = _concept()
    store.put(c)
    fresh = SqliteConceptStore(db)
    assert fresh.get(c.id).related == ["Chain Rule"]
    assert fresh.find_by_label("  BACKPROPAGATION ").id == c.id
    assert fresh.find_by_label("backprop") is None
    assert [x.label for x in fresh.all()] == ["Backpropagation"]


def test_event_log_order_and_persistence(tmp_path):
    db = tmp_path / "feynman.db"
    log = SqliteLearnerLog(db)
    cid = uuid4()
    log.append(ReviewEvent(concept_id=cid, concept_label="A", kind="explain", score=0.4))
    log.append(ReviewEvent(concept_id=cid, concept_label="A", kind="transfer", score=0.9))
    assert [e.kind for e in SqliteLearnerLog(db).events()] == ["explain", "transfer"]


def test_no_lost_updates_across_instances(tmp_path):
    # THE motivating property: with the JSON files, two open store instances clobbered each
    # other's whole-file writes. With SQLite upserts, both rows survive.
    db = tmp_path / "feynman.db"
    a, b = SqliteUserStateStore(db), SqliteUserStateStore(db)  # two "processes"
    uid = uuid4()
    c1, c2 = uuid4(), uuid4()
    a.put(UserState(concept_id=c1, user_id=uid, understanding_level=0.3))
    b.put(UserState(concept_id=c2, user_id=uid, understanding_level=0.7))  # b never saw c1
    fresh = SqliteUserStateStore(db)
    assert fresh.get(user_id=uid, concept_id=c1) is not None
    assert fresh.get(user_id=uid, concept_id=c2) is not None


def test_migration_imports_legacy_json_once(tmp_path):
    # seed a legacy JSON ledger
    c = _concept()
    JsonConceptStore(tmp_path / "feynman_concepts.json").put(c)
    uid = uuid4()
    JsonUserStateStore(tmp_path / "feynman_state.json").put(
        UserState(concept_id=c.id, user_id=uid, understanding_level=0.6,
                  last_reviewed_at=datetime.now(timezone.utc)))
    JsonLearnerLog(tmp_path / "feynman_learner.json").append(
        ReviewEvent(concept_id=c.id, concept_label=c.label, kind="explain", score=0.6))

    stores = stores_for(tmp_path)
    assert stores.concepts.find_by_label("backpropagation").id == c.id
    assert stores.states.get(user_id=uid, concept_id=c.id).understanding_level == 0.6
    assert len(stores.events.events()) == 1
    # originals renamed: never re-read, never double-imported
    assert not (tmp_path / "feynman_concepts.json").exists()
    assert (tmp_path / "feynman_concepts.json.imported").exists()
    again = stores_for(tmp_path)
    assert len(again.events.events()) == 1


def test_ledger_file_is_owner_only(tmp_path):
    import os

    db = tmp_path / "feynman.db"
    SqliteUserStateStore(db)
    assert os.stat(db).st_mode & 0o077 == 0  # personal learning data: no group/other access


def test_judge_model_env_override(monkeypatch):
    from feynman_loop.judge.claude_judge import ClaudeJudge
    from feynman_loop.transfer.claude_transfer import ClaudeTransfer

    monkeypatch.setenv("FEYNMAN_JUDGE_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # construction only; no call is made
    assert ClaudeJudge()._model == "claude-sonnet-4-6"
    assert ClaudeTransfer()._model == "claude-sonnet-4-6"
    monkeypatch.delenv("FEYNMAN_JUDGE_MODEL")
    assert ClaudeJudge()._model == "claude-opus-4-8"
