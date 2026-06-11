"""Vault and knowledge-map tests: markdown export with wikilinks, earned statuses, mermaid."""

from datetime import datetime, timedelta, timezone

from feynman_loop.learner import JsonLearnerLog, ReviewEvent
from feynman_loop.models import Concept, SourceRef, SourceTier, UserState
from feynman_loop.storage import JsonConceptStore, JsonIdentity, JsonUserStateStore
from feynman_loop.vault import mermaid_map, safe_name, status_of, sync_vault

_NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)


def _seed(root, label, *, interval_days=None, due=False, related=(), explanation=""):
    uid = JsonIdentity(root / "feynman_user.json").user_id()
    c = Concept(label=label, related=list(related),
                source_ref=SourceRef(tier=SourceTier.MODEL_FALLBACK,
                                     doc_label="general knowledge (unverified)", retrieval_query=label))
    JsonConceptStore(root / "feynman_concepts.json").put(c)
    if interval_days is not None:
        last = _NOW - timedelta(days=interval_days if due else 1)
        JsonUserStateStore(root / "feynman_state.json").put(UserState(
            concept_id=c.id, user_id=uid, understanding_level=0.72,
            last_reviewed_at=last, next_due_at=last + timedelta(days=interval_days)))
    if explanation:
        JsonLearnerLog(root / "feynman_learner.json").append(ReviewEvent(
            concept_id=c.id, concept_label=label, kind="explain", score=0.72, explanation=explanation))
    return c


def test_status_thresholds():
    assert status_of(interval_days=None, due_now=False, reviewed=False) == "untested"
    assert status_of(interval_days=5.0, due_now=True, reviewed=True) == "due"
    assert status_of(interval_days=1.0, due_now=False, reviewed=True) == "fragile"
    assert status_of(interval_days=10.0, due_now=False, reviewed=True) == "consolidating"
    assert status_of(interval_days=21.0, due_now=False, reviewed=True) == "strong"


def test_sync_vault_writes_markdown_with_wikilinks_and_index(tmp_path):
    _seed(tmp_path, "Backpropagation", interval_days=20, related=["Chain Rule"],
          explanation="gradients flow backward via the chain rule")
    vault = sync_vault(tmp_path, now=_NOW)

    note = (vault / "Backpropagation.md").read_text()
    assert "status: strong" in note
    assert "depth: working" in note
    assert "understanding: 72%" in note
    assert "[[Chain Rule]]" in note
    assert "gradients flow backward" in note          # their own words, in the note
    index = (vault / "Feynman Knowledge Map.md").read_text()
    assert "[[Backpropagation]]" in index and "## strong" in index


def test_untested_concept_appears_on_map(tmp_path):
    _seed(tmp_path, "Entropy")  # seeded at intake, never reviewed
    vault = sync_vault(tmp_path, now=_NOW)
    assert "status: untested" in (vault / "Entropy.md").read_text()


def test_mermaid_map_has_earned_nodes_frontier_and_edges(tmp_path):
    _seed(tmp_path, "Backpropagation", interval_days=20, related=["Chain Rule"])
    m = mermaid_map(tmp_path, now=_NOW)
    assert m.startswith("graph TD")
    assert 'Backpropagation 72% (strong)' in m
    assert '(("Chain Rule"))' in m                     # frontier node (not yet earned)
    assert "n_backpropagation --- n_chain_rule" in m   # the edge
    assert "class n_backpropagation strong" in m       # earned colour


def test_mermaid_map_empty(tmp_path):
    assert mermaid_map(tmp_path) == ""


def test_mermaid_escapes_double_quotes_in_labels(tmp_path):
    _seed(tmp_path, 'The "Attention" Trick', interval_days=20, related=['Q"K Scores'])
    m = mermaid_map(tmp_path, now=_NOW)
    assert "The 'Attention' Trick" in m       # rendered with safe quotes
    assert '"Attention"' not in m             # no raw double quotes inside node strings
    assert "Q'K Scores" in m                  # frontier label escaped too


def test_safe_name_strips_path_hostiles():
    assert "/" not in safe_name("TCP/IP Networking")
    assert safe_name("  ") == "concept"
