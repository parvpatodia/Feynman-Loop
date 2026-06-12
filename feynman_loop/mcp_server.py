"""Feynman-Loop as an MCP server: the loop lives inside your AI workflow (Claude Desktop, Cursor,
Claude Code) instead of a website you visit. The host calls these tools; the source is whatever is
already in your context (code, a paper, notes), so there's nothing to upload, and no source falls
back to general knowledge (tier-3).

Run (stdio): python -m feynman_loop.mcp_server

The host must PRESENT the probes/questions to the learner and must NOT answer them itself, the
whole point is that the learner retrieves the answer. Every tool result restates that.

TWO JUDGING MODES, decided by whether an API key is configured:
- independent (ANTHROPIC_API_KEY set): our judge model builds rubrics and scores. Strongest.
- zero-key (no key): the HOST model the user already pays for does the language work under a
  strict protocol, and THIS SERVER does the integrity work: every credited verdict must carry a
  verbatim evidence quote that the code verifies (verification.py), rubric sizes are enforced,
  and every score is computed in code. The host proposes; the code disposes. Softer than an
  independent judge (a lenient host can stretch within real quotes), so the ledger records the
  provenance of every event.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

import feynman_loop.loop as loop_ops
from feynman_loop import paths, providers
from feynman_loop.db import stores_for
from feynman_loop.judge.claude_judge import DEPTH_RANGES, ClaudeJudge, depth_spec
from feynman_loop.learner import (
    ClaudeMissTagger,
    ReviewEvent,
    derive_profile,
    streak_days,
    unlocked_milestones,
)
from feynman_loop.models import (
    MODEL_FALLBACK_LABEL,
    SNAPSHOT_LIMIT,
    Citation,
    Concept,
    GapReport,
    RubricPoint,
    SourceRef,
    SourceTier,
    TransferProbe,
    TransferResult,
)
from feynman_loop.relations import ClaudeRelatedConcepts
from feynman_loop.retrieval.base import RetrievedPassage
from feynman_loop.retrieval.query_expansion import ClaudeQueryExpander
from feynman_loop.transfer.claude_transfer import ClaudeTransfer
from feynman_loop.vault import mermaid_map, status_of, sync_vault
from feynman_loop.verification import STATUS_VALUE, evidence_supported, verified_status

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
    # WHY lazy: embeddings (torch) are an optional extra, only needed for long documents. The
    # common MCP path (a source pasted from chat) grounds directly, with no model load at all.
    from feynman_loop.retrieval.chroma_store import ChromaRetriever, sentence_transformer_embedder

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


def _independent_judge_available() -> bool:
    # WHY a wrapper: checked per call (not cached at import) and patchable in tests.
    return providers.has_api_key()


def _sync_vault() -> None:
    """Regenerate the knowledge-graph vault; best-effort, never blocks a review."""
    try:
        sync_vault(_ROOT)
    except Exception:
        pass


def _log_event(*, concept: Concept, kind: str, score: float, missed: list[str],
               explanation: str = "", rehearsed: bool = False,
               judge: str = "independent", mode: str = "full") -> None:
    """Append to the learner ledger; tagging is best-effort and never blocks the review.
    In zero-key mode the tagger has no client, so the try/except leaves tags empty."""
    try:
        tags = _make_tagger().tag(missed)
    except Exception:
        tags = []
    _make_learner_log().append(
        ReviewEvent(concept_id=concept.id, concept_label=concept.label, kind=kind, score=score,
                    missed=missed, tags=tags, explanation=explanation, rehearsed=rehearsed,
                    judge=judge, mode=mode)
    )
    _sync_vault()  # every attempt is reflected in the knowledge graph immediately


class _Check:
    def __init__(self, concept, passages=None):
        self.concept = concept
        self.passages = passages or []   # grounding passages; also what quotes are verified against
        self.probe = None
        self.remediation_done = False
        self.transfer_available = False
        # zero-key protocol phase: None | "rubric" | "judgment" | "transfer_probe" |
        # "transfer_judgment". The submit_* tools refuse any call that is out of phase, so a host
        # (or a user steering it) cannot skip a step or judge text the server never saw.
        self.awaiting = None
        self.pending_text = ""           # the explanation/answer locked in BEFORE judging starts
        # rapid mode (the volley): one question per rubric point, one-liner answers, judged
        # point-by-point. pos = which point is live; verdicts/answers accumulate to the same
        # scoring math and the same ledger write as a full explanation.
        self.rapid = None                # None | {"pos": int, "verdicts": [...], "answers": [...]}


_CHECKS: dict[str, _Check] = {}

# Sources up to this size are grounded DIRECTLY: the rubric sees the whole text, with no
# embedding model, no retrieval sampling, and no startup latency. Bigger sources go through
# the vector retriever (optional "embeddings" extra). Typical pasted sources are well under this.
_DIRECT_SOURCE_LIMIT = 12_000

def _split_passages(doc_id, doc_label: str, text: str) -> list[RetrievedPassage]:
    """Split a source into at most 6 passage blocks on paragraph boundaries. WHY whole-text
    blocks instead of top-k retrieval for normal sources: a rubric built from retrieved chunks
    can miss sections of the source entirely; seeing all of it yields a more accurate rubric."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()] or [text.strip()]
    blocks: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) > 2200:
            blocks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        blocks.append(cur)
    if len(blocks) > 6:
        blocks = blocks[:5] + ["\n\n".join(blocks[5:])]
    return [RetrievedPassage(doc_id=doc_id, doc_label=doc_label, text=b) for b in blocks]


def _ground(doc_id, doc_label: str, text: str, query: str) -> list[RetrievedPassage]:
    """Produce grounding passages for a source: directly for normal sizes, via the vector
    retriever for long documents, degrading honestly to the head of the text if the embeddings
    extra is not installed."""
    if len(text) <= _DIRECT_SOURCE_LIMIT:
        return _split_passages(doc_id, doc_label, text)
    try:
        retriever = _make_retriever()
    except Exception:
        return _split_passages(doc_id, doc_label, text[:_DIRECT_SOURCE_LIMIT])
    retriever.ingest(doc_id=doc_id, doc_label=doc_label, text=text)
    return retriever.retrieve(query=query, k=4)


def _ensure_passages(chk: _Check) -> None:
    """Re-derive grounding from the stored source snapshot. This is what makes grounded transfer
    work after a server restart: the snapshot outlives the process, so nothing is lost."""
    if chk.passages:
        return
    c = chk.concept
    if not c.source_text or c.source_ref.tier == SourceTier.MODEL_FALLBACK:
        return
    chk.passages = _ground(c.source_ref.doc_id or uuid.uuid4(),
                           c.source_ref.doc_label or f"{c.label} source",
                           c.source_text, c.source_ref.retrieval_query)


# --- zero-key protocol helpers: the host proposes, this code verifies and computes ---

_GENERIC_PROBE = "One required point is missing. What else would a complete explanation cover?"


def _points_from_host(points: list, passages, *, lo: int, hi: int) -> list[RubricPoint]:
    """Validate host-built rubric points. With passages, every quote must actually appear in one
    (verified by code; the citation is reassigned to the passage that matches). A point whose
    quote can't be found keeps its criterion but is flagged unverified general knowledge, so the
    host can't invent grounding. Raises ValueError when too few valid points remain."""
    built: list[RubricPoint] = []
    for raw in points or []:
        if not isinstance(raw, dict):
            continue
        criterion = " ".join(str(raw.get("criterion", "")).split())
        quote = str(raw.get("quote", "")).strip()
        question = " ".join(str(raw.get("question", "")).split())
        if len(criterion) < 8:
            continue  # "knows it" is not a checkable idea; drop trivial criteria
        citation = Citation(doc_label=MODEL_FALLBACK_LABEL, doc_id=None, quote=quote)
        if passages:
            try:
                idx = int(raw.get("passage_index", 0))
            except (TypeError, ValueError):
                idx = 0
            order = ([idx] + [i for i in range(len(passages)) if i != idx]
                     if 0 <= idx < len(passages) else list(range(len(passages))))
            match = next((i for i in order if evidence_supported(passages[i].text, quote)), None)
            if match is not None:
                p = passages[match]
                citation = Citation(doc_label=p.doc_label, doc_id=p.doc_id, quote=quote)
        built.append(RubricPoint(criterion=criterion, citation=citation, question=question))
    if len(built) < lo:
        raise ValueError(
            f"need at least {lo} valid rubric points for this depth, got {len(built)}; "
            "resubmit the full rubric"
        )
    return built[:hi]


def _host_verdicts(rubric, verdicts, text: str) -> list[tuple[str, bool, str]]:
    """Verify host verdicts against the locked-in text. Returns (status, evidence_ok, probe)
    triples aligned to the rubric, ready for loop.fold_verdicts. Unknown indexes are ignored;
    absent verdicts count as missed, so an incomplete submission can never inflate the score."""
    by_index: dict[int, dict] = {}
    for v in verdicts or []:
        if isinstance(v, dict):
            try:
                by_index[int(v.get("index"))] = v
            except (TypeError, ValueError):
                continue
    out: list[tuple[str, bool, str]] = []
    for i, _rp in enumerate(rubric):
        v = by_index.get(i, {})
        status, ok = verified_status(
            status=str(v.get("status", "missed")).strip().lower(),
            evidence=str(v.get("evidence", "")),
            text=text,
        )
        out.append((status, ok, str(v.get("probe", "")).strip()))
    return out


def _report_from_verdicts(chk: _Check, verdicts: list) -> GapReport:
    """Score in code from verified statuses, through the SAME fold as the independent judge."""
    rubric = chk.concept.rubric
    triples = _host_verdicts(rubric, verdicts, chk.pending_text)
    level, correct, gaps, failures = loop_ops.fold_verdicts(
        rubric, triples, fallback_probe=lambda rp: _GENERIC_PROBE)
    return GapReport(
        concept_id=chk.concept.id, user_explanation=chk.pending_text,
        understanding_level=level, correct_points=correct, gaps=gaps,
        evidence_failures=failures,
    )


def _status_of_state(state, *, now) -> str:
    """The concept's level, derived from its earned interval (same rules as the vault/map)."""
    if state is None or not (state.next_due_at and state.last_reviewed_at):
        return "untested"
    interval = (state.next_due_at - state.last_reviewed_at).total_seconds() / 86400
    return status_of(interval_days=interval, due_now=state.next_due_at <= now, reviewed=True)


def _progress_extras(*, prior_state=None, new_state=None) -> dict:
    """The visible-progression layer: streak, level changes, freshly unlocked milestones. All
    computed in code from the ledger; rewards consistency and territory, never the score, so
    there is nothing here a learner could game by gaming the judge."""
    events = _make_learner_log().events()
    out: dict = {"streak_days": streak_days(events)}
    fresh = [m for m in unlocked_milestones(events)
             if m not in unlocked_milestones(events[:-1])]
    if fresh:
        out["milestones"] = fresh
    if new_state is not None:
        now = datetime.now(timezone.utc)
        before = _status_of_state(prior_state, now=now)
        after = _status_of_state(new_state, now=now)
        if before != after:
            out["level"] = f"{before} -> {after}"
    return out


def _review_response(chk: _Check, report, state, rehearsed: bool, *, judge: str,
                     prior_state=None) -> dict:
    """One response shape for both judging modes, so the host-facing contract never forks."""
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
    resp = {
        "understanding_level": round(report.understanding_level, 2),
        "correct_points": report.correct_points,
        "gaps": [{"probe": g.description, "source": g.citation.doc_label} for g in report.gaps],
        "next_due": state.next_due_at.strftime("%Y-%m-%d") if state.next_due_at else "",
        "transfer_available": chk.transfer_available,
        "grounded": chk.concept.source_ref.tier != SourceTier.MODEL_FALLBACK,
        "instruction": _NO_ANSWER,
    }
    resp.update(_progress_extras(prior_state=prior_state, new_state=state))
    if judge == "host":
        resp["judge"] = "host-verified"
    if report.evidence_failures:
        resp["evidence_failures"] = report.evidence_failures
    return resp


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
    independent = _independent_judge_available()

    passages: list[RetrievedPassage] = []
    if source_text and source_text.strip():
        # new source provided -> (re)ground against it, and keep the snapshot so grounding
        # survives restarts (Decision 22)
        text = source_text.strip()[:SNAPSHOT_LIMIT]
        doc_id = uuid.uuid4()
        doc_label = f"{concept} source"
        # WHY: query expansion is an API call; zero-key retrieves with the concept label itself.
        query = _make_expander().expand(concept_label=concept) if independent else concept
        source_ref = SourceRef(
            tier=SourceTier.UPLOADED,
            doc_id=doc_id,
            doc_label=doc_label,
            retrieval_query=query,
        )
        passages = _ground(doc_id, doc_label, text, query)
        # WHY: keep the existing concept id so history/resurfacing stay attached
        update = {"source_ref": source_ref, "rubric": [], "depth": requested_depth, "source_text": text}
        c = existing.model_copy(update=update) if existing \
            else Concept(label=concept, source_ref=source_ref, depth=requested_depth, source_text=text)
        if independent:
            c.rubric = _make_judge().build_rubric(concept=c, passages=passages)
    elif existing and existing.rubric and not rebuild and not depth_changed:
        # WHY: returning concept, no new source -> reuse the persisted rubric as-is. Same id, same
        # history, and start is instant (no model call). Grounding for transfer is re-derived
        # lazily from the stored snapshot when needed (_ensure_passages).
        c = existing
    elif existing and existing.source_text:
        # rebuild/depth change with no new source, but a snapshot exists -> stay GROUNDED:
        # re-derive passages from the snapshot and rebuild the rubric against them.
        passages = _ground(existing.source_ref.doc_id or uuid.uuid4(),
                           existing.source_ref.doc_label or f"{concept} source",
                           existing.source_text, existing.source_ref.retrieval_query)
        c = existing.model_copy(update={"rubric": [], "depth": requested_depth})
        if independent:
            c.rubric = _make_judge().build_rubric(concept=c, passages=passages)
    else:
        source_ref = SourceRef(
            tier=SourceTier.MODEL_FALLBACK,
            doc_id=None,
            doc_label=MODEL_FALLBACK_LABEL,
            retrieval_query=concept,
        )
        # WHY on rebuild: keep the concept id (history stays attached) but flip the source tier to
        # model-fallback, because a no-source, no-snapshot rebuild cannot claim the old grounding.
        c = existing.model_copy(update={"rubric": [], "source_ref": source_ref, "depth": requested_depth}) \
            if existing else Concept(label=concept, source_ref=source_ref, depth=requested_depth)
        if independent:
            c.rubric = _make_judge().build_rubric(concept=c, passages=[])

    if independent and existing is None and not c.related:
        # graph edges, fetched once at intake; best-effort (a missing edge never blocks a check).
        # Zero-key: the host supplies related names in submit_rubric instead.
        try:
            c.related = _make_related().related_to(c.label)
        except Exception:
            c.related = []

    check_id = uuid.uuid4().hex
    chk = _Check(c, passages)
    _CHECKS[check_id] = chk
    base = {
        "check_id": check_id,
        "concept": c.label,
        "depth": c.depth,
        "grounded": c.source_ref.tier != SourceTier.MODEL_FALLBACK,
        "returning_concept": existing is not None,
    }

    if c.rubric:
        # rubric ready: the independent judge built it, or a returning concept reused its own
        _make_concept_store().put(c)
        _sync_vault()  # the node appears on the map at intake, as "untested"
        return {
            **base,
            "instruction": "Ask the learner to explain this concept in their own words, then call judge_explanation.",
        }

    # zero-key: the HOST builds the rubric now, under verification. The concept is persisted only
    # when submit_rubric accepts it, so no half-built concept ever enters the ledger.
    chk.awaiting = "rubric"
    lo, _hi = DEPTH_RANGES.get(c.depth, DEPTH_RANGES["working"])
    count, scope = depth_spec(c.depth)
    grounding = (
        "Each point: 'criterion' (one checkable idea), 'passage_index' (the numbered passage "
        "grounding it), 'quote' (VERBATIM from that passage; the server verifies every quote "
        "and flags any it cannot find)."
        if passages else
        "There is no source, so ground each point in your own knowledge: set 'quote' to a brief "
        "supporting fact and 'passage_index' to 0; points are flagged as unverified general knowledge."
    )
    return {
        **base,
        "mode": "zero-key: you are the judging model; the server verifies evidence and computes all scores",
        "action": "build_rubric",
        "passages": [{"index": i, "text": p.text} for i, p in enumerate(passages)],
        "instruction": (
            "No API key is configured, so YOU are the judging model under a verified protocol. "
            f"Build the scoring rubric NOW, silently, before the learner explains: {count} points "
            f"({lo} minimum) covering {scope} {grounding} "
            "Also include 'related': 3 to 6 directly related concept names. "
            "Call submit_rubric, then ask the learner to explain. Never show them the rubric."
        ),
    }


@mcp.tool()
def submit_rubric(check_id: str, points: list[dict], related: list[str] | None = None) -> dict:
    """Zero-key mode only: store the rubric YOU built after start_check returned action
    "build_rubric". points = [{"criterion", "passage_index", "quote"}]; related = 3-6 related
    concept names. The server verifies every quote and enforces the depth's minimum point count."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id; call start_check first"}
    if chk.awaiting != "rubric":
        return {"error": "not awaiting a rubric; this tool only follows a start_check that asked for one"}
    lo, hi = DEPTH_RANGES.get(chk.concept.depth, DEPTH_RANGES["working"])
    try:
        chk.concept.rubric = _points_from_host(points, chk.passages, lo=lo, hi=hi)
    except ValueError as e:
        return {"error": str(e)}  # awaiting stays "rubric": fix and resubmit
    if not chk.concept.related and related:
        # WHY sanitized: graph node names come from the host here; cap and trim so a runaway
        # list or a paragraph-sized "name" can't pollute the vault.
        clean = [" ".join(str(r).split())[:60] for r in related if str(r).strip()]
        chk.concept.related = list(dict.fromkeys(clean))[:6]
    chk.awaiting = None
    _make_concept_store().put(chk.concept)
    _sync_vault()  # the node appears on the map at intake, as "untested"
    verified = sum(1 for rp in chk.concept.rubric if rp.citation.doc_label != MODEL_FALLBACK_LABEL)
    return {
        "rubric_points": len(chk.concept.rubric),
        "grounded_points": verified if chk.passages else None,
        "instruction": ("Rubric stored. Ask the learner to explain the concept in their own "
                        "words, then call judge_explanation with their explanation."),
    }


@mcp.tool()
def judge_explanation(check_id: str, explanation: str) -> dict:
    """Score the learner's explanation against the concept's fixed rubric. Returns the gaps as
    PROBES (questions). Relay the probes to the learner; do NOT answer them yourself. In zero-key
    mode this instead locks the explanation in and returns a judging protocol for YOU to execute;
    follow it and call submit_judgment."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id; call start_check first"}
    if chk.awaiting == "rubric":
        return {"error": "rubric not built yet; finish submit_rubric first"}
    # WHY these two guards: every in-flight step must finish or the ledger double-logs. A volley
    # mid-flight would record this attempt twice (once here, once at _finish_rapid); a pending
    # zero-key step would have its locked text silently discarded.
    if chk.rapid is not None:
        return {"error": "a rapid volley is in progress on this check; finish it with answer() "
                         "or start a fresh check"}
    if chk.awaiting is not None:
        return {"error": f"finish the current step first (awaiting {chk.awaiting})"}

    if not _independent_judge_available():
        # WHY locked first: the explanation is stored BEFORE the rubric/protocol is revealed, so
        # the text being judged can never be tailored to what the judge is about to look for.
        chk.pending_text = explanation
        chk.awaiting = "judgment"
        return {
            "action": "judge_in_host",
            "rubric": [{"index": i, "criterion": rp.criterion}
                       for i, rp in enumerate(chk.concept.rubric)],
            "instruction": (
                "Judge the locked-in explanation STRICTLY against each numbered point NOW. For "
                "each index return: 'status' (met/partial/missed; near-verbatim copying of the "
                "source is partial at best), 'evidence' (for met/partial, a VERBATIM quote from "
                "the LEARNER'S explanation; the server verifies it and downgrades any credit it "
                "cannot find), 'probe' (for points not met, a question that prompts the learner "
                "to retrieve the idea WITHOUT revealing it). Call submit_judgment with all "
                "verdicts. Do not soften: an inflated score destroys this record's value."
            ),
        }

    uid = _make_identity().user_id()
    prior = _make_store().get(user_id=uid, concept_id=chk.concept.id)
    report, state, rehearsed = loop_ops.run_review(
        concept=chk.concept, user_id=uid, explanation=explanation,
        judge=_make_judge(), store=_make_store(),
    )
    chk.transfer_available = report.understanding_level >= loop_ops.TRANSFER_GATE
    chk.probe = None
    # ledger: criteria not fully met = rubric minus the points credited as "met"
    met = set(report.correct_points)
    missed = [rp.criterion for rp in chk.concept.rubric if rp.criterion not in met]
    _log_event(concept=chk.concept, kind="explain", score=report.understanding_level,
               missed=missed, explanation=explanation, rehearsed=rehearsed)
    return _review_response(chk, report, state, rehearsed, judge="independent", prior_state=prior)


@mcp.tool()
def submit_judgment(check_id: str, verdicts: list[dict]) -> dict:
    """Zero-key mode only: submit the verdicts YOU produced after judge_explanation or
    score_transfer returned a judging protocol. verdicts = [{"index", "status", "evidence",
    "probe"}]. The server verifies every evidence quote and computes the score in code."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id; call start_check first"}

    if chk.awaiting == "judgment":
        report = _report_from_verdicts(chk, verdicts)
        uid = _make_identity().user_id()
        prior = _make_store().get(user_id=uid, concept_id=chk.concept.id)
        state, rehearsed = loop_ops.record_review(
            concept=chk.concept, user_id=uid,
            explanation=chk.pending_text, report=report, store=_make_store(),
        )
        chk.transfer_available = report.understanding_level >= loop_ops.TRANSFER_GATE
        chk.probe = None
        chk.awaiting = None
        met = set(report.correct_points)
        missed = [rp.criterion for rp in chk.concept.rubric if rp.criterion not in met]
        _log_event(concept=chk.concept, kind="explain", score=report.understanding_level,
                   missed=missed, explanation=chk.pending_text, rehearsed=rehearsed,
                   judge="host")
        return _review_response(chk, report, state, rehearsed, judge="host", prior_state=prior)

    if chk.awaiting == "transfer_judgment":
        rubric = chk.probe.rubric
        triples = _host_verdicts(rubric, verdicts, chk.pending_text)
        failures = sum(1 for _s, ok, _p in triples if not ok)
        total, met, missed_points = 0.0, [], []
        for (status, _ok, _probe), rp in zip(triples, rubric, strict=True):
            value = STATUS_VALUE[status]
            total += value
            if value >= 1.0:
                met.append(rp.criterion)
            else:
                missed_points.append(rp)
        result = TransferResult(
            concept_id=chk.probe.concept_id, question=chk.probe.question,
            user_answer=chk.pending_text, transfer_score=total / len(rubric),
            met=met, missed=missed_points,
        )
        uid = _make_identity().user_id()
        prior = _make_store().get(user_id=uid, concept_id=chk.concept.id)
        loop_ops.record_transfer_result(result=result, user_id=uid, store=_make_store())
        chk.awaiting = None
        _log_event(concept=chk.concept, kind="transfer", score=result.transfer_score,
                   missed=[m.criterion for m in result.missed], explanation=chk.pending_text,
                   judge="host")
        resp = {
            "transfer_score": round(result.transfer_score, 2),
            "met": result.met,
            "missed": [{"criterion": m.criterion, "source": m.citation.doc_label}
                       for m in result.missed],
            "judge": "host-verified",
            "instruction": _NO_ANSWER,
        }
        resp.update(_progress_extras(
            prior_state=prior, new_state=_make_store().get(user_id=uid, concept_id=chk.concept.id)))
        if failures:
            resp["evidence_failures"] = failures
        if (result.transfer_score < loop_ops.REMEDIATION_GATE
                and not chk.remediation_done and result.missed):
            # one bounded retry, generated by the host under the same verified-probe protocol
            chk.remediation_done = True
            chk.awaiting = "transfer_probe"
            resp["action"] = "make_remediation"
            resp["instruction"] = (
                "The learner fell short. Silently create ONE narrower retry focused ONLY on the "
                "missed points: call submit_transfer_probe with a new question and 1 to 3 points "
                "targeting them, then relay the question. Never answer it. "
            ) + _NO_ANSWER
        return resp

    return {"error": "nothing awaiting judgment; follow a judging protocol from "
                     "judge_explanation or score_transfer first"}


def _ensure_questions(chk: _Check) -> None:
    """Backfill per-point questions for rubrics built before rapid mode; persisted once."""
    rubric = chk.concept.rubric
    if all(rp.question for rp in rubric):
        return
    try:
        qs = _make_judge().make_point_questions(
            concept_label=chk.concept.label, criteria=[rp.criterion for rp in rubric])
    except Exception:
        qs = ["" for _ in rubric]
    for rp, q in zip(rubric, qs, strict=True):
        if not rp.question:
            rp.question = q or (f"In one or two sentences, explain the next aspect of "
                                f"{chk.concept.label}.")
    _make_concept_store().put(chk.concept)


def _finish_rapid(chk: _Check, *, judge: str) -> dict:
    """Close the volley: the SAME scoring fold, gated scheduling, and ledger as a full review."""
    rubric = chk.concept.rubric
    rapid = chk.rapid
    level, correct, gaps, failures = loop_ops.fold_verdicts(
        rubric, rapid["verdicts"],
        fallback_probe=lambda rp: rp.question or _GENERIC_PROBE)
    report = GapReport(
        concept_id=chk.concept.id,
        user_explanation="\n".join(rapid["answers"]),  # their words, per point, for the journey
        understanding_level=level,
        correct_points=correct, gaps=gaps, evidence_failures=failures,
    )
    uid = _make_identity().user_id()
    prior = _make_store().get(user_id=uid, concept_id=chk.concept.id)
    state, rehearsed = loop_ops.record_review(
        concept=chk.concept, user_id=uid,
        explanation=report.user_explanation, report=report, store=_make_store(),
    )
    chk.transfer_available = report.understanding_level >= loop_ops.TRANSFER_GATE
    chk.rapid = None
    chk.probe = None
    met = set(correct)
    missed = [rp.criterion for rp in rubric if rp.criterion not in met]
    _log_event(concept=chk.concept, kind="explain", score=report.understanding_level,
               missed=missed, explanation=report.user_explanation, rehearsed=rehearsed,
               judge=judge, mode="rapid")
    resp = _review_response(chk, report, state, rehearsed, judge=judge, prior_state=prior)
    resp["done"] = True
    return resp


@mcp.tool()
def quick_check(concept: str, source_text: str = "", depth: str = "") -> dict:
    """The 2-minute volley: one sharp question per rubric point, answered in a line or two each,
    judged point by point. Same honest scoring and ledger as a full check, a fraction of the
    friction. Use this by default; use start_check when the learner wants to compose a full
    explanation. Relay each question and collect the LEARNER's answer; never answer yourself."""
    started = start_check(concept, source_text=source_text, depth=depth)
    if "error" in started or started.get("action") == "build_rubric":
        if started.get("action") == "build_rubric":
            started["instruction"] += " Then call quick_check again to begin the volley."
        return started
    chk = _CHECKS[started["check_id"]]
    chk.rapid = {"pos": 0, "verdicts": [], "answers": []}
    n = len(chk.concept.rubric)

    if not _independent_judge_available():
        return {
            **started,
            "mode": "rapid (zero-key: YOU judge each answer; the server verifies and scores)",
            "total_questions": n,
            "points": [{"index": i, "criterion": rp.criterion}
                       for i, rp in enumerate(chk.concept.rubric)],
            "instruction": (
                "Run the volley NOW, one point at a time and in order. For each point: write ONE "
                "short question yourself that prompts the idea WITHOUT revealing the criterion, "
                "ask the learner, then call answer(check_id, response, status, evidence, probe) "
                "with the learner's answer and YOUR strict verdict (evidence = verbatim quote "
                "from their answer; probe = a retrieval question if not met). The server "
                "verifies evidence and computes all scores. Keep the pace fast. Never answer "
                "for the learner."
            ),
        }

    _ensure_questions(chk)
    return {
        **started,
        "mode": "rapid",
        "total_questions": n,
        "question": chk.concept.rubric[0].question,
        "instruction": ("Volley of " + str(n) + " quick questions. Relay this question, collect "
                        "the learner's short answer, call answer(check_id, response). Keep the "
                        "pace fast; one line is enough. ") + _NO_ANSWER,
    }


@mcp.tool()
def answer(check_id: str, response: str, status: str = "", evidence: str = "", probe: str = "") -> dict:
    """Submit the learner's answer to the current volley question (rapid mode). Returns the
    verdict and the next question, or the final scorecard. In zero-key mode YOU supply status
    (met/partial/missed), evidence (verbatim quote from the answer), and probe; the server
    verifies the evidence and computes the score either way."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id; call quick_check first"}
    if chk.rapid is None:
        return {"error": "no volley running; call quick_check first"}
    rubric = chk.concept.rubric
    i = chk.rapid["pos"]
    rp = rubric[i]
    independent = _independent_judge_available()  # once per turn; the mode never flips mid-call

    if independent:
        verdict_status, ok, verdict_probe = _make_judge().evaluate_point(
            criterion=rp.criterion, question=rp.question, answer=response)
        judge = "independent"
    else:
        if status not in STATUS_VALUE:
            return {"error": "zero-key volley: pass status=met|partial|missed with evidence "
                             "quoted verbatim from the learner's answer"}
        verdict_status, ok = verified_status(status=status, evidence=evidence, text=response)
        verdict_probe = " ".join(probe.split())
        judge = "host"

    chk.rapid["verdicts"].append((verdict_status, ok, verdict_probe))
    chk.rapid["answers"].append(response)
    chk.rapid["pos"] = i + 1

    if chk.rapid["pos"] >= len(rubric):
        return _finish_rapid(chk, judge=judge)

    nxt = rubric[chk.rapid["pos"]]
    out = {
        "verdict": verdict_status,
        "progress": f"{chk.rapid['pos']}/{len(rubric)}",
        "instruction": "Relay the next question; keep the pace. " + _NO_ANSWER,
    }
    if not ok:
        out["evidence_failures"] = 1
    if independent:
        out["next_question"] = nxt.question
    else:
        out["next_point"] = {"index": chk.rapid["pos"], "criterion": nxt.criterion}
    return out


@mcp.tool()
def make_transfer(check_id: str) -> dict:
    """Generate a transfer challenge (a novel application question) once the explanation is solid.
    Relay the question to the learner; do not answer it. In zero-key mode this returns a protocol
    for YOU to create the challenge; follow it and call submit_transfer_probe."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id"}
    if not chk.transfer_available:
        return {"error": "transfer not unlocked yet; the explanation wasn't solid enough"}
    if chk.rapid is not None or chk.awaiting is not None:
        # WHY: never clobber an in-flight step; the locked text/protocol would be silently lost
        return {"error": "finish the current step first"}
    # WHY remediation_done is NOT reset here: the retry budget is one per check session, not one
    # per challenge. Resetting it made the bound farmable by requesting fresh challenges.
    _ensure_passages(chk)  # restores grounding from the stored snapshot after a restart

    if not _independent_judge_available():
        chk.awaiting = "transfer_probe"
        return {
            "action": "make_transfer_in_host",
            "passages": [{"index": i, "text": p.text} for i, p in enumerate(chk.passages)],
            "instruction": (
                "Silently create the transfer challenge NOW: ONE novel application question the "
                "source does not directly answer (a new scenario, a prediction, an edge case, or "
                "a debugging task), plus 1 to 4 rubric points in submit_rubric's format "
                "('criterion', 'passage_index', 'quote'; without passages, 'quote' is a brief "
                "supporting fact). Call submit_transfer_probe, then relay the question it "
                "returns. Never answer it."
            ),
        }

    chk.probe = _make_transfer().generate_probe(concept=chk.concept, passages=chk.passages)
    return {"question": chk.probe.question, "instruction": _NO_ANSWER}


@mcp.tool()
def submit_transfer_probe(check_id: str, question: str, points: list[dict]) -> dict:
    """Zero-key mode only: store the transfer challenge YOU created after make_transfer (or a
    remediation request) returned a protocol. The server verifies the rubric quotes; the question
    is then relayed to the learner."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id"}
    if chk.awaiting != "transfer_probe":
        return {"error": "not awaiting a transfer challenge; call make_transfer first"}
    question = " ".join(question.split())
    if len(question) < 15:
        return {"error": "question too short to be a real challenge; resubmit"}
    try:
        rubric = _points_from_host(points, chk.passages, lo=1, hi=4)
    except ValueError as e:
        return {"error": str(e)}
    chk.probe = TransferProbe(concept_id=chk.concept.id, question=question, rubric=rubric)
    chk.awaiting = None
    return {
        "question": question,
        "instruction": ("Relay this question to the learner and collect THEIR answer, then call "
                        "score_transfer with it. ") + _NO_ANSWER,
    }


@mcp.tool()
def score_transfer(check_id: str, answer: str) -> dict:
    """Score the learner's answer to the transfer challenge. May return one narrower retry question
    (remediation_question) if they fell short; relay it without answering. In zero-key mode this
    locks the answer in and returns a judging protocol; follow it and call submit_judgment."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id"}
    if chk.probe is None:
        return {"error": "no transfer to score; call make_transfer first"}
    if chk.awaiting is not None:
        # WHY: scoring against a probe while another step is pending would judge stale state
        return {"error": f"finish the current step first (awaiting {chk.awaiting})"}

    if not _independent_judge_available():
        chk.pending_text = answer
        chk.awaiting = "transfer_judgment"
        return {
            "action": "judge_transfer_in_host",
            "rubric": [{"index": i, "criterion": rp.criterion}
                       for i, rp in enumerate(chk.probe.rubric)],
            "instruction": (
                "Judge the locked-in transfer answer STRICTLY against each numbered point NOW. "
                "For each index return: 'status' (met/partial/missed), 'evidence' (for "
                "met/partial, a VERBATIM quote from the LEARNER'S answer; the server verifies "
                "it), 'probe' (optional here). Call submit_judgment with all verdicts. Do not "
                "soften the verdicts."
            ),
        }

    uid = _make_identity().user_id()
    prior = _make_store().get(user_id=uid, concept_id=chk.concept.id)
    result = loop_ops.score_transfer(
        probe=chk.probe, user_id=uid, user_answer=answer,
        engine=_make_transfer(), store=_make_store(),
    )
    _log_event(concept=chk.concept, kind="transfer", score=result.transfer_score,
               missed=[m.criterion for m in result.missed], explanation=answer)
    remediation_question = None
    if result.transfer_score < loop_ops.REMEDIATION_GATE and not chk.remediation_done and result.missed:
        chk.remediation_done = True
        chk.probe = _make_transfer().generate_remediation(
            concept=chk.concept, passages=chk.passages, missed=result.missed
        )
        remediation_question = chk.probe.question
    resp = {
        "transfer_score": round(result.transfer_score, 2),
        "met": result.met,
        "missed": [{"criterion": m.criterion, "source": m.citation.doc_label} for m in result.missed],
        "remediation_question": remediation_question,
        "instruction": _NO_ANSWER,
    }
    resp.update(_progress_extras(
        prior_state=prior, new_state=_make_store().get(user_id=uid, concept_id=chk.concept.id)))
    return resp


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
    all_events = _make_learner_log().events()  # loaded once; reused for the card's streak
    events = [e for e in all_events if e.concept_label.strip().casefold() == wanted]
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

    # The journey card: a shareable artifact of the 0-to-90 arc. Growth and consistency only,
    # never a rank against anyone (Decision 8).
    card = None
    c = _make_concept_store().find_by_label(events[0].concept_label)
    if explains and c is not None:
        now = datetime.now(timezone.utc)
        st = _make_store().get(user_id=_make_identity().user_id(), concept_id=c.id)
        level = _status_of_state(st, now=now)
        strength = ""
        if st and st.next_due_at and st.last_reviewed_at:
            days = (st.next_due_at - st.last_reviewed_at).total_seconds() / 86400
            strength = f" | memory: {days:.0f} days"
        arc = (f"{explains[0].score:.0%} -> {explains[-1].score:.0%}" if len(explains) >= 2
               else f"{explains[0].score:.0%}")
        streak = streak_days(all_events)
        card = (f"**{c.label}** ({c.depth})\n"
                f"{arc} across {len(explains)} attempt(s) | level: {level}{strength}\n"
                f"day streak: {streak} | Feynman-Loop")
    return {"concept": concept, "attempts": attempts, "headline": headline, "card": card,
            "instruction": "If the learner asks to share or celebrate progress, render the card as a quote block."}


if __name__ == "__main__":
    mcp.run()
