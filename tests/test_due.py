"""Tests for the due CLI: ledger aggregation, the SessionStart context block, and the
consume-once semantics of pending shipped-work nudges."""

import json
from datetime import datetime, timedelta, timezone

from feynman_loop.due import _context_block, collect
from feynman_loop.models import Citation, Concept, RubricPoint, SourceRef, SourceTier, UserState
from feynman_loop.storage import JsonConceptStore, JsonIdentity, JsonUserStateStore

_NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _seed(root, label="Backpropagation", due_delta_days=-1, understanding=0.45):
    uid = JsonIdentity(root / "feynman_user.json").user_id()
    c = Concept(
        label=label,
        source_ref=SourceRef(tier=SourceTier.MODEL_FALLBACK, doc_label="general knowledge (unverified)",
                             retrieval_query=label),
        rubric=[RubricPoint(criterion="x", citation=Citation(doc_label="d", quote="q"))],
    )
    JsonConceptStore(root / "feynman_concepts.json").put(c)
    JsonUserStateStore(root / "feynman_state.json").put(UserState(
        concept_id=c.id, user_id=uid, understanding_level=understanding,
        next_due_at=_NOW + timedelta(days=due_delta_days),
    ))
    return c


def test_collect_flags_due_concepts(tmp_path):
    _seed(tmp_path, due_delta_days=-1)          # overdue
    _seed(tmp_path, label="IPO", due_delta_days=+5)  # not due yet
    data = collect(root=tmp_path, now=_NOW)
    assert data["tracked"] == 2
    assert [d["concept"] for d in data["due"]] == ["Backpropagation"]


def test_context_block_offers_and_never_forces(tmp_path):
    _seed(tmp_path, due_delta_days=-1)
    block = _context_block(collect(root=tmp_path, now=_NOW))
    assert "due for an explain-back" in block
    assert "OFFER" in block and "never answer them yourself" in block
    assert "stays due" in block  # declining is allowed; natural consequence, not punishment


def test_context_block_empty_when_nothing_actionable(tmp_path):
    _seed(tmp_path, due_delta_days=+5)  # tracked but not due
    assert _context_block(collect(root=tmp_path, now=_NOW)) == ""


def test_pending_is_surfaced_once_then_consumed(tmp_path):
    (tmp_path / "feynman_pending.json").write_text(json.dumps({
        "items": [{"at": "2026-06-10T00:00:00Z", "cwd": "/proj", "lines": 240, "files": ["api.py"]}],
    }))
    first = collect(root=tmp_path, now=_NOW)
    assert first["pending"][0]["lines"] == 240
    assert "240 AI-written lines" in _context_block(first)
    second = collect(root=tmp_path, now=_NOW)
    assert second["pending"] == []  # consumed: surfaced once, never nags twice
