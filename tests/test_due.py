"""Tests for the due CLI: ledger aggregation, the SessionStart context block, the
consume-once semantics of pending shipped-work nudges, and the notification push."""

import json
from datetime import datetime, timedelta, timezone

from feynman_loop.due import _context_block, _notification_text, collect
from feynman_loop.models import Citation, Concept, RubricPoint, SourceRef, SourceTier, UserState
from feynman_loop.storage import JsonConceptStore, JsonIdentity, JsonUserStateStore

_NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _seed(root, label="Backpropagation", due_delta_days=-1, understanding=0.45, gaps=()):
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
        identified_gaps=list(gaps),
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


def test_context_opens_with_the_weakest_concepts_stored_probe(tmp_path):
    _seed(tmp_path, label="Entropy", due_delta_days=-1, understanding=0.7,
          gaps=["What does sharpening a distribution do to surprise?"])
    _seed(tmp_path, label="Backpropagation", due_delta_days=-2, understanding=0.4,
          gaps=["What performs the weight update after the gradients exist?"])
    data = collect(root=tmp_path, now=_NOW)
    assert [d["concept"] for d in data["due"]] == ["Backpropagation", "Entropy"]  # weakest first
    block = _context_block(data)
    # the nudge is a concrete 30-second question, not a guilt counter
    assert "Micro-rep" in block
    assert "What performs the weight update" in block
    assert "from memory" in block


def test_notification_text_is_one_concrete_question(tmp_path):
    _seed(tmp_path, gaps=["What performs the weight update after the gradients exist?"])
    text = _notification_text(collect(root=tmp_path, now=_NOW))
    assert text.startswith("Backpropagation: What performs the weight update")
    assert len(text) <= 180


def test_notification_text_empty_when_nothing_due(tmp_path):
    _seed(tmp_path, due_delta_days=+5)
    assert _notification_text(collect(root=tmp_path, now=_NOW)) == ""


def test_pending_is_surfaced_once_then_consumed(tmp_path):
    (tmp_path / "feynman_pending.json").write_text(json.dumps({
        "items": [{"at": "2026-06-10T00:00:00Z", "cwd": "/proj", "lines": 240, "files": ["api.py"]}],
    }))
    first = collect(root=tmp_path, now=_NOW)
    assert first["pending"][0]["lines"] == 240
    assert "240 AI-written lines" in _context_block(first)
    second = collect(root=tmp_path, now=_NOW)
    assert second["pending"] == []  # consumed: surfaced once, never nags twice


def test_pending_shipped_work_bridges_into_the_loop(tmp_path):
    """A shipped-work nudge must FEED the loop, not dead-end as a guilt notice. It tells the host
    to ground an explain-back in the very file that was shipped (server stores no code; the host
    reads it live), and it stays an offer, never a gate."""
    (tmp_path / "feynman_pending.json").write_text(json.dumps({
        "items": [{"at": "2026-06-10T00:00:00Z", "cwd": "/proj", "lines": 240,
                   "files": ["auth.py", "models.py"]}],
    }))
    block = _context_block(collect(root=tmp_path, now=_NOW))
    assert "240 AI-written lines" in block   # the existing notice is preserved
    assert "source_text" in block            # bridged into the loop, grounded in the shipped code
    assert "read auth.py" in block           # names the concrete artifact, not "your code"
    assert "Do not force it" in block        # still an offer (trust criterion / no forced interruption)


def test_applescript_string_survives_hostile_text():
    from feynman_loop.due import _applescript_string

    s = _applescript_string('Poincaré said "non-ASCII" stays\x00 literal \\ here')
    assert s.startswith('"') and s.endswith('"')
    assert "Poincaré" in s                  # non-ASCII passes through raw (no \\uXXXX)
    assert '\\"non-ASCII\\"' in s           # quotes escaped the AppleScript way
    assert "\x00" not in s                  # control chars dropped, not \\u-escaped
    assert "\\\\ here" in s                 # backslash doubled
