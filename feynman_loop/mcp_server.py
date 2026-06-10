"""Feynman-Loop as an MCP server: the loop lives inside your AI workflow (Claude Desktop, Cursor,
Claude Code) instead of a website you visit. The host calls these tools; the source is whatever is
already in your context (code, a paper, notes), so there's nothing to upload, and no source falls
back to general knowledge (tier-3).

Run (stdio): python -m feynman_loop.mcp_server

The host must PRESENT the probes/questions to the learner and must NOT answer them itself, the
whole point is that the learner retrieves the answer. Every tool result restates that.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

import feynman_loop.loop as loop_ops
from feynman_loop.judge.claude_judge import ClaudeJudge
from feynman_loop.models import MODEL_FALLBACK_LABEL, Concept, SourceRef, SourceTier
from feynman_loop.retrieval.chroma_store import ChromaRetriever, sentence_transformer_embedder
from feynman_loop.retrieval.query_expansion import ClaudeQueryExpander
from feynman_loop.storage import JsonUserStateStore
from feynman_loop.transfer.claude_transfer import ClaudeTransfer

mcp = FastMCP("feynman-loop")

# WHY: host instruction repeated on every result — a friction tool inside a helpful assistant
# only works if the assistant relays the prompts instead of answering them.
_NO_ANSWER = (
    "Present this to the learner and have THEM answer in their own words. Do not answer for them; "
    "the entire point is that they retrieve it themselves."
)

_USER_ID = uuid.uuid4()  # single local user for this MCP server


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


def _make_store():
    return JsonUserStateStore("feynman_state.json")


class _Check:
    def __init__(self, concept, retriever):
        self.concept = concept
        self.retriever = retriever
        self.probe = None
        self.remediation_done = False
        self.transfer_available = False


_CHECKS: dict[str, _Check] = {}


@mcp.tool()
def start_check(concept: str, source_text: str = "") -> dict:
    """Start a Feynman-Loop check on a concept. Pass source_text = the relevant material already in
    context (code, a paper, notes) to ground the check in it; leave it empty to be tested from
    general knowledge. Returns a check_id for the other tools."""
    if source_text and source_text.strip():
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
    else:
        retriever = None
        source_ref = SourceRef(
            tier=SourceTier.MODEL_FALLBACK,
            doc_id=None,
            doc_label=MODEL_FALLBACK_LABEL,
            retrieval_query=concept,
        )

    c = Concept(label=concept, source_ref=source_ref)
    loop_ops.build_concept_rubric(concept=c, retriever=retriever, judge=_make_judge())
    check_id = uuid.uuid4().hex
    _CHECKS[check_id] = _Check(c, retriever)
    return {
        "check_id": check_id,
        "concept": concept,
        "grounded": source_ref.tier != SourceTier.MODEL_FALLBACK,
        "instruction": "Ask the learner to explain this concept in their own words, then call judge_explanation.",
    }


@mcp.tool()
def judge_explanation(check_id: str, explanation: str) -> dict:
    """Score the learner's explanation against the concept's key points. Returns the gaps as PROBES
    (questions). Relay the probes to the learner; do NOT answer them yourself."""
    chk = _CHECKS.get(check_id)
    if chk is None:
        return {"error": "unknown check_id; call start_check first"}
    report, state = loop_ops.run_review(
        concept=chk.concept, user_id=_USER_ID, explanation=explanation,
        judge=_make_judge(), store=_make_store(),
    )
    chk.transfer_available = report.understanding_level >= loop_ops.TRANSFER_GATE
    chk.probe = None
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
        probe=chk.probe, user_id=_USER_ID, user_answer=answer, engine=_make_transfer(), store=_make_store()
    )
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
    """Show what the learner has been tested on and what's due now, the memory of their
    understanding over time. Surface the due items to prompt a review."""
    now = datetime.now(timezone.utc)
    store = _make_store()
    concepts = []
    for chk in _CHECKS.values():
        st = store.get(user_id=_USER_ID, concept_id=chk.concept.id)
        if st is None:
            continue
        concepts.append({
            "concept": chk.concept.label,
            "understanding_level": round(st.understanding_level, 2),
            "transfer_level": round(st.transfer_level, 2) if st.transfer_level is not None else None,
            "next_due": st.next_due_at.strftime("%Y-%m-%d") if st.next_due_at else "",
            "due_now": bool(st.next_due_at and st.next_due_at <= now),
        })
    return {"concepts": concepts}


if __name__ == "__main__":
    mcp.run()
