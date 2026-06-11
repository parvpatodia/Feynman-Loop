"""Feynman-Loop as an MCP server: the loop lives inside your AI workflow (Claude Desktop, Cursor,
Claude Code) instead of a website you visit. The host calls these tools; the source is whatever is
already in your context (code, a paper, notes), so there's nothing to upload, and no source falls
back to general knowledge (tier-3).

Run (stdio): python -m feynman_loop.mcp_server

The host must PRESENT the probes/questions to the learner and must NOT answer them itself, the
whole point is that the learner retrieves the answer. Every tool result restates that.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

import feynman_loop.loop as loop_ops
from feynman_loop import paths
from feynman_loop.db import stores_for
from feynman_loop.judge.claude_judge import ClaudeJudge
from feynman_loop.learner import ClaudeMissTagger, ReviewEvent, derive_profile
from feynman_loop.models import MODEL_FALLBACK_LABEL, Concept, SourceRef, SourceTier
from feynman_loop.relations import ClaudeRelatedConcepts
from feynman_loop.retrieval.chroma_store import ChromaRetriever, sentence_transformer_embedder
from feynman_loop.retrieval.query_expansion import ClaudeQueryExpander
from feynman_loop.transfer.claude_transfer import ClaudeTransfer
from feynman_loop.vault import mermaid_map, sync_vault

mcp = FastMCP("feynman-loop")

# WHY: host instruction repeated on every result — a friction tool inside a helpful assistant
# only works if the assistant relays the prompts instead of answering them.
_NO_ANSWER = (
    "Present this to the learner and have THEM answer in their own words. Do not answer for them; "
    "the entire point is that they retrieve it themselves."
)

# WHY: absolute, env-driven home (FEYNMAN_HOME, default ~/.feynman-loop). Claude launches the
# server from an arbitrary cwd, and a pip-installed package must never write into site-packages.
_ROOT = paths.home()


# --- factories (tests override these to inject fakes) ---
def _make_retriever():
    return ChromaRetriever(
        embed=sentence_transformer_embedder(), collection_name=f"mcp_{uuid.uuid4().hex}"
    )


def _make_judge():
    return ClaudeJudge()


def _make_transfer():
    return ClaudeTransfer()


def _make_expander():
    return ClaudeQueryExpander()


# Storage goes through db.stores_for: one SQLite ledger (WAL) shared safely by every surface,
# replacing the JSON files whose whole-file writes lost updates under concurrent processes.
def _make_store():
    return stores_for(_ROOT).states


def _make_concept_store():
    return stores_for(_ROOT).concepts


def _make_learner_log():
    return stores_for(_ROOT).events


def _make_tagger():
    return ClaudeMissTagger()


def _make_identity():
    # WHY: a STABLE local identity. A per-process uuid would orphan the user's entire history
    # on every server restart, silently destroying the memory-over-time moat.
    return stores_for(_ROOT).identity


def _make_related():
    return ClaudeRelatedConcepts()


def _sync_vault() -> None:
    """Regenerate the knowledge-graph vault; best-effort, never blocks a review."""
    try:
        sync_vault(_ROOT)
    except Exception:
        pass


def _log_event(*, concept: Concept, kind: str, score: float, missed: list[str],
               explanation: str = "", rehearsed: bool = False) -> None:
    """Append to the learner ledger; tagging is best-effort and never blocks the review."""
    try:
        tags = _make_tagger().tag(missed)
    except Exception:
        tags = []
    _make_learner_log().append(
        ReviewEvent(concept_id=concept.id, concept_label=concept.label, kind=kind, score=score,
                    missed=missed, tags=tags, explanation=explanation, rehearsed=rehearsed)
    )
    _sync_vault()  # every attempt is reflected in the knowledge graph immediately


class _Check:
    def __init__(self, concept, retriever):
        self.concept = concept
        self.retriever = retriever
        self.probe = None
        self.remediation_done = False
        self.transfer_available = False


_CHECKS: dict[str, _Check] = {}


@mcp.tool()
def start_check(concept: str, source_text: str = "", rebuild: bool = False, depth: str = "") -> dict:
    """Start a Feynman-Loop check on a concept. Pass source_text = the relevant material already in
    context (code, a paper, notes) to ground the check in it; leave it empty to be tested from
    general knowledge. A concept the learner has checked before continues its existing history.
    depth sets the bar the learner is aiming for: "overview" (big picture), "working" (default;
    the mechanism), or "expert" (boundary conditions and failure modes too). Changing depth on a
    known concept rebuilds its rubric. rebuild=True re-derives the rubric (use when one seems off
    or stale); without a new source the rebuilt rubric comes from general knowledge, flagged.
    Returns a check_id for the other tools."""
    concept = " ".join(concept.split())  # normalize: "Backprop\n " and "backprop" are one concept
    existing = _make_concept_store().find_by_label(concept)
    requested_depth = depth if depth in ("overview", "working", "expert") \
        else (existing.depth if existing else "working")
    depth_changed = existing is not None and requested_depth != existing.depth

    if source_text and source_text.strip():
        # new source provided -> (re)ground: ingest and rebuild the rubric against it
        retriever = _make_retriever()
        doc_id = uuid.uuid4()
        doc_label = f"{concept} source"
        retriever.ingest(doc_id=doc_id, doc_label=doc_label, text=source_text)
        source_ref = SourceRef(
            tier=SourceTier.UPLOADED,
            doc_id=doc_id,
            doc_label=doc_label,
            retrieval_query=_make_expander().expand(concept_label=concept),
        )
        # WHY: keep the existing concept id so history/resurfacing stay attached
        c = existing.model_copy(update={"source_ref": source_ref, "rubric": [], "depth": requested_depth}) \
            if existing else Concept(label=concept, source_ref=source_ref, depth=requested_depth)
        loop_ops.build_concept_rubric(concept=c, retriever=retriever, judge=_make_judge())
    elif existing and existing.rubric and not rebuild and not depth_changed:
        # WHY: returning concept, no new source -> reuse the persisted rubric as-is. Same id, same
        # history, and start is instant (no model call). NOTE: the vector index is in-memory, so a
        # previously-grounded concept can't re-retrieve after a restart; the rubric (built from the
        # source, citations intact) still scores reviews; transfer falls back to knowledge mode.
        retriever = None
        c = existing
    else:
        retriever = None
        source_ref = SourceRef(
            tier=SourceTier.MODEL_FALLBACK,
            doc_id=None,
            doc_label=MODEL_FALLBACK_LABEL,
            retrieval_query=concept,
        )
        # WHY on rebuild: keep the concept id (history stays attached) but flip the source tier to
        # model-fallback, because a no-source rebuild cannot honestly claim the old grounding.
        c = existing.model_copy(update={"rubric": [], "source_ref": source_ref, "depth": requested_depth}) \
            if existing else Concept(label=concept, source_ref=source_ref, depth=requested_depth)
        loop_ops.build_concept_rubric(concept=c, retriever=retriever, judge=_make_judge())

    if existing is None and not c.related:
        # graph edges, fetched once at intake; best-effort (a missing edge never blocks a check)
        try:
            c.related = _make_related().related_to(c.label)
        except Exception:
            c.related = []

    _make_concept_store().put(c)
    _sync_vault()  # the node appears on the map at intake, as "untested"
    check_id = uuid.uuid4().hex
    _CHECKS[check_id] = _Check(c, retriever)
    return {
        "check_id": check_id,
        "concept": c.label,
        "depth": c.depth,
        "grounded": c.source_ref.tier != SourceTier.MODEL_FALLBACK,
        "returning_concept": existing is not None,
        "instruction": "Ask the learner to explain this concept in their own words, then call judge_explanation.",
    }


@mcp.tool()
def judge_explanation(check_id: str, explanation: str) -> dict:
    """Score the learner's explanation against the concept's key points. Returns the gaps as PROBES
    (questions). Relay the probes to the learner; do NOT answer them yourself."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id; call start_check first"}
    report, state, rehearsed = loop_ops.run_review(
        concept=chk.concept, user_id=_make_identity().user_id(), explanation=explanation,
        judge=_make_judge(), store=_make_store(),
    )
    chk.transfer_available = report.understanding_level >= loop_ops.TRANSFER_GATE
    chk.probe = None
    # ledger: criteria not fully met = rubric minus the points credited as "met"
    met = set(report.correct_points)
    missed = [rp.criterion for rp in chk.concept.rubric if rp.criterion not in met]
    _log_event(concept=chk.concept, kind="explain", score=report.understanding_level,
               missed=missed, explanation=explanation, rehearsed=rehearsed)
    if rehearsed:
        return {
            "understanding_level": round(report.understanding_level, 2),
            "rehearsed": True,
            "instruction": (
                "This is nearly identical to the learner's previous attempt, so it proves recall "
                "of their own wording, not understanding. Ask them to RE-EXPRESS it: a new "
                "example, a different audience, or an analogy they haven't used. Do not repeat "
                "the gaps and never answer for them."
            ),
        }
    return {
        "understanding_level": round(report.understanding_level, 2),
        "correct_points": report.correct_points,
        "gaps": [{"probe": g.description, "source": g.citation.doc_label} for g in report.gaps],
        "next_due": state.next_due_at.strftime("%Y-%m-%d") if state.next_due_at else "",
        "transfer_available": chk.transfer_available,
        "grounded": chk.concept.source_ref.tier != SourceTier.MODEL_FALLBACK,
        "instruction": _NO_ANSWER,
    }


@mcp.tool()
def make_transfer(check_id: str) -> dict:
    """Generate a transfer challenge (a novel application question) once the explanation is solid.
    Relay the question to the learner; do not answer it."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id"}
    if not chk.transfer_available:
        return {"error": "transfer not unlocked yet; the explanation wasn't solid enough"}
    chk.probe = loop_ops.generate_transfer_probe(
        concept=chk.concept, retriever=chk.retriever, engine=_make_transfer()
    )
    chk.remediation_done = False
    return {"question": chk.probe.question, "instruction": _NO_ANSWER}


@mcp.tool()
def score_transfer(check_id: str, answer: str) -> dict:
    """Score the learner's answer to the transfer challenge. May return one narrower retry question
    (remediation_question) if they fell short; relay it without answering."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id"}
    if chk.probe is None:
        return {"error": "no transfer to score; call make_transfer first"}
    result = loop_ops.score_transfer(
        probe=chk.probe, user_id=_make_identity().user_id(), user_answer=answer,
        engine=_make_transfer(), store=_make_store(),
    )
    _log_event(concept=chk.concept, kind="transfer", score=result.transfer_score,
               missed=[m.criterion for m in result.missed], explanation=answer)
    remediation_question = None
    if result.transfer_score < loop_ops.REMEDIATION_GATE and not chk.remediation_done and result.missed:
        chk.remediation_done = True
        chk.probe = loop_ops.generate_remediation_probe(
            concept=chk.concept, retriever=chk.retriever, engine=_make_transfer(), missed=result.missed
        )
        remediation_question = chk.probe.question
    return {
        "transfer_score": round(result.transfer_score, 2),
        "met": result.met,
        "missed": [{"criterion": m.criterion, "source": m.citation.doc_label} for m in result.missed],
        "remediation_question": remediation_question,
        "instruction": _NO_ANSWER,
    }


@mcp.tool()
def progress() -> dict:
    """Show the learner's understanding ledger: every concept ever checked, what's due now, and
    the learner profile (recurring failure modes, explain-vs-apply gap). Persisted across
    sessions. Surface the due items to prompt a review."""
    now = datetime.now(timezone.utc)
    store = _make_store()
    uid = _make_identity().user_id()
    concepts = []
    # WHY: read from DISK, not the in-process _CHECKS, so the ledger survives restarts. This is
    # the memory-over-time moat; it was previously in-memory only, which made it vanish silently.
    for c in _make_concept_store().all():
        st = store.get(user_id=uid, concept_id=c.id)
        if st is None:
            continue
        # WHY: the interval IS the consolidation metric. It can only grow through repeated,
        # delayed, successful retrieval (gated_next_due), so "comes back every N days" is the
        # honest headline of memory strength; per-attempt % is just one performance.
        interval_days = None
        if st.next_due_at and st.last_reviewed_at:
            interval_days = round((st.next_due_at - st.last_reviewed_at).total_seconds() / 86400, 1)
        concepts.append({
            "concept": c.label,
            "memory_strength_days": interval_days,
            "understanding_level": round(st.understanding_level, 2),
            "transfer_level": round(st.transfer_level, 2) if st.transfer_level is not None else None,
            "next_due": st.next_due_at.strftime("%Y-%m-%d") if st.next_due_at else "",
            "due_now": bool(st.next_due_at and st.next_due_at <= now),
        })
    due = [c["concept"] for c in concepts if c["due_now"]]
    return {
        "concepts": concepts,
        "due_now": due,
        "learner": derive_profile(_make_learner_log().events()),
    }


@mcp.tool()
def knowledge_map() -> dict:
    """Render the learner's verified knowledge graph: every node was earned by explaining, with
    status from earned memory strength (due/fragile/consolidating/strong); circles are the
    frontier (related concepts not yet learned). Render the mermaid code block for the user."""
    mermaid = mermaid_map(_ROOT)
    if not mermaid:
        return {"note": "no concepts tracked yet; run a check first"}
    vault = os.environ.get("FEYNMAN_VAULT", str(_ROOT / "vault"))
    return {
        "mermaid": mermaid,
        "vault": vault,
        "instruction": (
            "Show the mermaid graph to the user as a rendered diagram. Mention they can open "
            f"the markdown vault at {vault} in Obsidian for the full interactive graph."
        ),
    }


@mcp.tool()
def journey(concept: str) -> dict:
    """Show the learner's journey on one concept: every past attempt in their own words, with
    scores, oldest first. Present it as their growth record; this is the 0-to-90 made visible."""
    wanted = concept.strip().casefold()
    events = [e for e in _make_learner_log().events()
              if e.concept_label.strip().casefold() == wanted]
    if not events:
        return {"concept": concept, "attempts": [], "note": "no attempts recorded yet"}
    attempts = [{
        "at": e.at.strftime("%Y-%m-%d"),
        "kind": e.kind,
        "score": round(e.score, 2),
        "rehearsed": e.rehearsed,
        "their_words": (e.explanation[:200] + "...") if len(e.explanation) > 200 else e.explanation,
    } for e in events]
    explains = [e for e in events if e.kind == "explain"]
    headline = None
    if len(explains) >= 2:
        headline = f"{explains[0].score:.0%} on {explains[0].at:%Y-%m-%d} -> {explains[-1].score:.0%} today's latest"
    return {"concept": concept, "attempts": attempts, "headline": headline}


if __name__ == "__main__":
    mcp.run()
