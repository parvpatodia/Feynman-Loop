"""FastAPI backend for the explain-it-back web demo.

Exposes the existing pipeline (ingest -> review -> transfer) as JSON endpoints and serves the
single-page frontend. Per-session state (retriever + concept + the pending transfer probe) is
held in memory, keyed by session_id, which is fine for a single-user demo.

Component construction goes through the _make_* factories so tests can inject fakes and run
offline without tokens or model downloads.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from feynman_loop.judge.claude_judge import ClaudeJudge
from feynman_loop.loop import (
    REMEDIATION_GATE,
    TRANSFER_GATE,
    build_concept_rubric,
    generate_remediation_probe,
    generate_transfer_probe,
    run_review,
    score_transfer,
)
from feynman_loop.models import (
    MODEL_FALLBACK_LABEL,
    SNAPSHOT_LIMIT,
    Concept,
    SourceRef,
    SourceTier,
    TransferProbe,
)
from feynman_loop import paths
from feynman_loop.db import stores_for
from feynman_loop.learner import ClaudeMissTagger, ReviewEvent
from feynman_loop.relations import ClaudeRelatedConcepts
from feynman_loop.retrieval.chroma_store import ChromaRetriever, sentence_transformer_embedder
from feynman_loop.retrieval.query_expansion import ClaudeQueryExpander
from feynman_loop.sources import extract_text
from feynman_loop.transfer.claude_transfer import ClaudeTransfer
from feynman_loop.vault import sync_vault

_STATIC = Path(__file__).parent / "static"
_ROOT = paths.home()  # env-driven (FEYNMAN_HOME), never the package location


def _clean(text: str) -> str:
    return " ".join(text.split())


# --- factories (tests override these to inject fakes) ---
def _make_retriever():
    return ChromaRetriever(
        embed=sentence_transformer_embedder(),
        collection_name=f"web_{uuid.uuid4().hex}",
    )


def _make_judge():
    return ClaudeJudge()


def _make_transfer():
    return ClaudeTransfer()


def _make_store():
    return stores_for(_ROOT).states


def _make_expander():
    return ClaudeQueryExpander()


def _make_concept_store():
    return stores_for(_ROOT).concepts


def _make_learner_log():
    return stores_for(_ROOT).events


def _make_tagger():
    return ClaudeMissTagger()


def _make_identity():
    # WHY: one stable identity shared with the MCP server, so web and MCP write the SAME
    # understanding ledger instead of forking per-session users.
    return stores_for(_ROOT).identity


def _make_related():
    return ClaudeRelatedConcepts()


def _sync_vault() -> None:
    try:
        sync_vault(_ROOT)
    except Exception:
        pass


def _log_event(*, concept: Concept, kind: str, score: float, missed: list[str],
               explanation: str = "", rehearsed: bool = False) -> None:
    try:
        tags = _make_tagger().tag(missed)
    except Exception:
        tags = []
    _make_learner_log().append(
        ReviewEvent(concept_id=concept.id, concept_label=concept.label, kind=kind, score=score,
                    missed=missed, tags=tags, explanation=explanation, rehearsed=rehearsed)
    )
    _sync_vault()  # every attempt is reflected in the knowledge graph immediately


# --- in-memory per-session state ---
class _Session:
    def __init__(self, retriever, concept, user_id, store):
        self.retriever = retriever
        self.concept = concept
        self.user_id = user_id
        self.store = store
        self.probe: TransferProbe | None = None  # set after a gated review
        self.remediation_done = False  # WHY: bound remediation to a single retry, not a loop
        self.transfer_available = False  # WHY: review sets this; the probe is generated on demand


_SESSIONS: dict[str, _Session] = {}


# --- request / response models ---
class StartRequest(BaseModel):
    source_text: str = ""  # optional; empty -> tier-3 (no source, judged on model knowledge)
    concept_label: str
    depth: str = "working"  # overview | working | expert (the bar the rubric is built to)


class StartResponse(BaseModel):
    session_id: str
    concept_label: str


class ReviewRequest(BaseModel):
    session_id: str
    explanation: str


class GapOut(BaseModel):
    description: str   # a probe (question), not the missing fact verbatim
    doc_label: str


class ReviewResponse(BaseModel):
    understanding_level: float
    correct_points: list[str]
    gaps: list[GapOut]
    next_due: str
    review_count: int
    transfer_available: bool  # whether a transfer challenge is unlocked (generated on demand)
    grounded: bool  # False when judged on model knowledge (tier-3), not the user's own source
    rehearsed: bool = False  # near-verbatim repeat of the prior attempt: ask to re-express


class GenerateTransferRequest(BaseModel):
    session_id: str


class TransferQuestionResponse(BaseModel):
    question: str


class TransferRequest(BaseModel):
    session_id: str
    answer: str


class MissOut(BaseModel):
    criterion: str
    doc_label: str
    quote: str


class TransferResponse(BaseModel):
    transfer_score: float
    met: list[str]
    missed: list[MissOut]
    remediation_question: str | None = None  # a narrower retry, offered once when transfer is weak


app = FastAPI(title="Feynman-Loop")


def _session(sid: str) -> _Session:
    s = _SESSIONS.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return s


def _start_session(*, source_text: str, concept_label: str, depth: str = "working") -> StartResponse:
    concept_label = " ".join(concept_label.split())  # one concept per label, however it's typed
    existing = _make_concept_store().find_by_label(concept_label)
    requested_depth = depth if depth in ("overview", "working", "expert") \
        else (existing.depth if existing else "working")
    depth_changed = existing is not None and requested_depth != existing.depth

    if source_text and source_text.strip():
        # grounded path: ingest the source, derive the retrieval query from the concept
        retriever = _make_retriever()
        doc_id = uuid.uuid4()
        doc_label = f"{concept_label} source"
        retriever.ingest(doc_id=doc_id, doc_label=doc_label, text=source_text)
        source_ref = SourceRef(
            tier=SourceTier.UPLOADED,
            doc_id=doc_id,
            doc_label=doc_label,
            retrieval_query=_make_expander().expand(concept_label=concept_label),
        )
        # WHY: keep the existing concept id so the history stays attached to one concept.
        # The snapshot keeps grounding restart-proof on this surface too (parity with MCP).
        snapshot = source_text.strip()[:SNAPSHOT_LIMIT]
        concept = existing.model_copy(update={"source_ref": source_ref, "rubric": [],
                                              "depth": requested_depth, "source_text": snapshot}) \
            if existing else Concept(label=concept_label, source_ref=source_ref,
                                     depth=requested_depth, source_text=snapshot)
        build_concept_rubric(concept=concept, retriever=retriever, judge=_make_judge())
    elif existing and existing.rubric and not depth_changed:
        # returning concept, no new source -> reuse the persisted rubric (instant start, same history)
        retriever = None
        concept = existing
    else:
        # tier-3 (confirmed): no source -> the rubric is built from model knowledge, flagged.
        # retriever=None flows through as empty passages -> knowledge mode in judge/transfer.
        retriever = None
        source_ref = SourceRef(
            tier=SourceTier.MODEL_FALLBACK,
            doc_id=None,
            doc_label=MODEL_FALLBACK_LABEL,
            retrieval_query=concept_label,
        )
        concept = existing.model_copy(update={"rubric": [], "source_ref": source_ref, "depth": requested_depth}) \
            if existing else Concept(label=concept_label, source_ref=source_ref, depth=requested_depth)
        build_concept_rubric(concept=concept, retriever=retriever, judge=_make_judge())

    if existing is None and not concept.related:
        try:
            concept.related = _make_related().related_to(concept.label)
        except Exception:
            concept.related = []

    _make_concept_store().put(concept)
    _sync_vault()
    sid = uuid.uuid4().hex
    _SESSIONS[sid] = _Session(retriever, concept, _make_identity().user_id(), _make_store())
    return StartResponse(session_id=sid, concept_label=concept.label)


@app.post("/api/session", response_model=StartResponse)
def start(req: StartRequest) -> StartResponse:
    return _start_session(source_text=req.source_text, concept_label=req.concept_label, depth=req.depth)


@app.post("/api/session/upload", response_model=StartResponse)
async def start_upload(
    concept_label: str = Form(...), file: UploadFile = File(...),  # noqa: B008 (FastAPI idiom)
    depth: str = Form("working"),  # noqa: B008 (FastAPI idiom)
) -> StartResponse:
    data = await file.read()
    try:
        text = extract_text(filename=file.filename or "upload", data=data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _start_session(source_text=text, concept_label=concept_label, depth=depth)


@app.post("/api/review", response_model=ReviewResponse)
def review(req: ReviewRequest) -> ReviewResponse:
    s = _session(req.session_id)
    report, state, rehearsed = run_review(
        concept=s.concept,
        user_id=s.user_id,
        explanation=req.explanation,
        judge=_make_judge(),
        store=s.store,
    )

    # WHY (latency): do NOT generate the transfer probe here. Returning the gap after a single
    # model call lets the user read it immediately; the transfer question is generated in a
    # separate request (/api/transfer/generate) while they read, instead of blocking this one.
    s.transfer_available = report.understanding_level >= TRANSFER_GATE
    s.probe = None
    met = set(report.correct_points)
    _log_event(concept=s.concept, kind="explain", score=report.understanding_level,
               missed=[rp.criterion for rp in s.concept.rubric if rp.criterion not in met],
               explanation=req.explanation, rehearsed=rehearsed)

    return ReviewResponse(
        understanding_level=report.understanding_level,
        correct_points=[_clean(p) for p in report.correct_points],
        gaps=[
            GapOut(description=_clean(g.description), doc_label=g.citation.doc_label)
            for g in report.gaps
        ],
        next_due=state.next_due_at.strftime("%Y-%m-%d") if state.next_due_at else "",
        review_count=state.review_count,
        transfer_available=s.transfer_available,
        grounded=s.concept.source_ref.tier != SourceTier.MODEL_FALLBACK,
        rehearsed=rehearsed,
    )


@app.post("/api/transfer/generate", response_model=TransferQuestionResponse)
def generate_transfer(req: GenerateTransferRequest) -> TransferQuestionResponse:
    s = _session(req.session_id)
    if not s.transfer_available:
        raise HTTPException(status_code=409, detail="no transfer available for this review")
    s.probe = generate_transfer_probe(
        concept=s.concept, retriever=s.retriever, engine=_make_transfer()
    )
    s.remediation_done = False  # a freshly generated probe re-opens the one-shot remediation
    return TransferQuestionResponse(question=_clean(s.probe.question))


@app.post("/api/transfer", response_model=TransferResponse)
def transfer(req: TransferRequest) -> TransferResponse:
    s = _session(req.session_id)
    if s.probe is None:
        raise HTTPException(status_code=409, detail="no transfer probe for this session")
    result = score_transfer(
        probe=s.probe, user_id=s.user_id, user_answer=req.answer, engine=_make_transfer(), store=s.store
    )
    _log_event(concept=s.concept, kind="transfer", score=result.transfer_score,
               missed=[m.criterion for m in result.missed], explanation=req.answer)

    remediation_question = None
    if result.transfer_score < REMEDIATION_GATE and not s.remediation_done and result.missed:
        # WHY: one bounded retry focused on what they missed; the next probe becomes the active one.
        s.remediation_done = True
        s.probe = generate_remediation_probe(
            concept=s.concept, retriever=s.retriever, engine=_make_transfer(), missed=result.missed
        )
        remediation_question = _clean(s.probe.question)

    return TransferResponse(
        transfer_score=result.transfer_score,
        met=[_clean(m) for m in result.met],
        missed=[
            MissOut(criterion=_clean(m.criterion), doc_label=m.citation.doc_label, quote=_clean(m.citation.quote))
            for m in result.missed
        ],
        remediation_question=remediation_question,
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")
