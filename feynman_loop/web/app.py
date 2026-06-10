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

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from feynman_loop.judge.claude_judge import ClaudeJudge
from feynman_loop.loop import TRANSFER_GATE, generate_transfer_probe, run_review, score_transfer
from feynman_loop.models import Concept, SourceRef, SourceTier, TransferProbe
from feynman_loop.retrieval.chroma_store import ChromaRetriever, sentence_transformer_embedder
from feynman_loop.storage import JsonUserStateStore
from feynman_loop.transfer.claude_transfer import ClaudeTransfer

_STATIC = Path(__file__).parent / "static"


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
    return JsonUserStateStore("feynman_state.json")


# --- in-memory per-session state ---
class _Session:
    def __init__(self, retriever, concept, user_id, store):
        self.retriever = retriever
        self.concept = concept
        self.user_id = user_id
        self.store = store
        self.probe: TransferProbe | None = None  # set after a gated review


_SESSIONS: dict[str, _Session] = {}


# --- request / response models ---
class StartRequest(BaseModel):
    source_text: str
    concept_label: str
    retrieval_query: str


class StartResponse(BaseModel):
    session_id: str
    concept_label: str


class ReviewRequest(BaseModel):
    session_id: str
    explanation: str


class GapOut(BaseModel):
    description: str
    doc_label: str
    quote: str


class ReviewResponse(BaseModel):
    understanding_level: float
    correct_points: list[str]
    gaps: list[GapOut]
    next_due: str
    review_count: int
    transfer_question: str | None  # present only when the gate is passed


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


app = FastAPI(title="Feynman-Loop")


def _session(sid: str) -> _Session:
    s = _SESSIONS.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return s


@app.post("/api/session", response_model=StartResponse)
def start(req: StartRequest) -> StartResponse:
    retriever = _make_retriever()
    doc_id = uuid.uuid4()
    doc_label = f"{req.concept_label} source"
    retriever.ingest(doc_id=doc_id, doc_label=doc_label, text=req.source_text)
    concept = Concept(
        label=req.concept_label,
        source_ref=SourceRef(
            tier=SourceTier.UPLOADED,
            doc_id=doc_id,
            doc_label=doc_label,
            retrieval_query=req.retrieval_query,
        ),
    )
    sid = uuid.uuid4().hex
    _SESSIONS[sid] = _Session(retriever, concept, uuid.uuid4(), _make_store())
    return StartResponse(session_id=sid, concept_label=concept.label)


@app.post("/api/review", response_model=ReviewResponse)
def review(req: ReviewRequest) -> ReviewResponse:
    s = _session(req.session_id)
    report, state = run_review(
        concept=s.concept,
        user_id=s.user_id,
        explanation=req.explanation,
        retriever=s.retriever,
        judge=_make_judge(),
        store=s.store,
    )

    transfer_question = None
    if report.understanding_level >= TRANSFER_GATE:
        s.probe = generate_transfer_probe(
            concept=s.concept, retriever=s.retriever, engine=_make_transfer()
        )
        transfer_question = _clean(s.probe.question)

    return ReviewResponse(
        understanding_level=report.understanding_level,
        correct_points=[_clean(p) for p in report.correct_points],
        gaps=[
            GapOut(description=_clean(g.description), doc_label=g.citation.doc_label, quote=_clean(g.citation.quote))
            for g in report.gaps
        ],
        next_due=state.next_due_at.strftime("%Y-%m-%d") if state.next_due_at else "",
        review_count=state.review_count,
        transfer_question=transfer_question,
    )


@app.post("/api/transfer", response_model=TransferResponse)
def transfer(req: TransferRequest) -> TransferResponse:
    s = _session(req.session_id)
    if s.probe is None:
        raise HTTPException(status_code=409, detail="no transfer probe for this session")
    result = score_transfer(
        probe=s.probe, user_id=s.user_id, user_answer=req.answer, engine=_make_transfer(), store=s.store
    )
    return TransferResponse(
        transfer_score=result.transfer_score,
        met=[_clean(m) for m in result.met],
        missed=[
            MissOut(criterion=_clean(m.criterion), doc_label=m.citation.doc_label, quote=_clean(m.citation.quote))
            for m in result.missed
        ],
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")
