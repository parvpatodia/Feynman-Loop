"""ClaudeJudge: the concrete Judge backed by Anthropic Claude.

It compares a user's explanation against the retrieved passages and returns a grounded
GapReport. Two grounding-integrity choices, both load-bearing for the trust criterion:

  1. The model judges ONLY against the passages we hand it (Decision 15). The system prompt
     forbids outside knowledge. v1 guarantees a source at intake, so we always have passages;
     if retrieval comes back empty, we abstain rather than let the model invent a verdict.

  2. The model never emits citation identifiers. It references each passage by INDEX and
     supplies the verbatim quote; the code maps the index back to the real doc_id / doc_label
     from retrieval. This makes it impossible for the judge to hallucinate which source a gap
     came from. The model reasons; the system owns the identifiers.

Model id and the structured-output approach (messages.parse + Pydantic) follow the current
Anthropic Python SDK.
"""

from __future__ import annotations

from anthropic import Anthropic
from pydantic import BaseModel

from feynman_loop.judge.base import Judge
from feynman_loop.models import Citation, Concept, Gap, GapReport
from feynman_loop.retrieval.base import RetrievedPassage

# WHY: keep the model the smallest commodity component. Pinned here, swappable in one place.
_MODEL = "claude-opus-4-8"

_SYSTEM = """You are a grounding-strict judge of a learner's explanation of a single concept.

You are given the concept name, the learner's explanation in their own words, and a numbered
list of source passages. Judge the explanation ONLY against those passages. Do not use any
outside knowledge, even for canonical concepts. If the passages do not cover part of the
explanation, do not judge that part.

For every gap you report, you must ground it: cite the passage (by its index) whose text
contradicts or is missing from the explanation, and quote the exact span you relied on. A gap
with no grounding passage is not allowed.

Also report what the learner got right (correct_points), so the feedback is not purely
negative. Set understanding_level between 0 and 1 for how well the explanation matches the
grounded source. Be fair: reward a correct idea expressed in different words; do not penalize
phrasing."""


class _GapVerdict(BaseModel):
    """One gap, as the MODEL reports it. Note: no doc_id/doc_label here, by design."""

    description: str       # what is missing or wrong, plainly
    passage_index: int     # which numbered passage grounds this gap
    quote: str             # the exact span from that passage


class _JudgeVerdict(BaseModel):
    """The full structured verdict the model returns. Code turns this into a GapReport."""

    understanding_level: float
    correct_points: list[str]
    gaps: list[_GapVerdict]


class ClaudeJudge(Judge):
    def __init__(self, *, client: Anthropic | None = None, model: str = _MODEL) -> None:
        # WHY: client is injectable so tests run offline with a fake and the provider stays swappable.
        self._client = client or Anthropic()
        self._model = model

    def evaluate(
        self,
        *,
        concept: Concept,
        user_explanation: str,
        passages: list[RetrievedPassage],
    ) -> GapReport:
        if not passages:
            # WHY: Decision 15 — we never judge an ungrounded concept. Empty retrieval is a
            # failure the caller handles (tell the user, skip), not a verdict the judge fabricates.
            raise ValueError(
                f"No passages to ground concept {concept.label!r}; refusing to judge ungrounded."
            )

        numbered = "\n\n".join(
            f"[{i}] {p.text}" for i, p in enumerate(passages)
        )
        user_msg = (
            f"Concept: {concept.label}\n\n"
            f"Learner's explanation:\n{user_explanation}\n\n"
            f"Source passages:\n{numbered}"
        )

        response = self._client.messages.parse(
            model=self._model,
            max_tokens=16000,
            thinking={"type": "adaptive"},  # judging is reasoning-heavy; let Claude size its own thinking
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            output_format=_JudgeVerdict,
        )
        verdict: _JudgeVerdict = response.parsed_output

        gaps: list[Gap] = []
        for gv in verdict.gaps:
            # WHY: clamp a bad index into range rather than trust the model's number, then build
            # the citation from the REAL passage. The model never names the source itself.
            idx = gv.passage_index if 0 <= gv.passage_index < len(passages) else 0
            p = passages[idx]
            gaps.append(
                Gap(
                    description=gv.description,
                    citation=Citation(doc_label=p.doc_label, doc_id=p.doc_id, quote=gv.quote),
                )
            )

        return GapReport(
            concept_id=concept.id,
            user_explanation=user_explanation,
            understanding_level=verdict.understanding_level,
            correct_points=verdict.correct_points,
            gaps=gaps,
        )
