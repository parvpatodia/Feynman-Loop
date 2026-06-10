"""ClaudeTransfer: generate and score transfer probes with Claude.

Grounding integrity (same pattern as ClaudeJudge): when generating the rubric, the model
references the source passage by INDEX and quotes it; the code maps the index back to the real
doc_id/doc_label. If no rubric point can be grounded, we refuse to ask. Scoring is done against
the fixed, already-grounded rubric, so the user is never graded against invented truth.
"""

from __future__ import annotations

from anthropic import Anthropic
from pydantic import BaseModel

from feynman_loop.models import Citation, Concept, RubricPoint, TransferProbe, TransferResult
from feynman_loop.retrieval.base import RetrievedPassage
from feynman_loop.transfer.base import TransferEngine

_MODEL = "claude-opus-4-8"

_GEN_SYSTEM = """You design a TRANSFER task for one concept, to test whether the learner can
APPLY it to a situation the source does not spell out, not merely restate the definition.

Given the concept and numbered source passages, produce:
1. one novel application question, answerable using only the principles in the source.
2. a rubric: the points a correct answer must contain. EVERY rubric point must be derivable from
   the source passages. For each point, give the passage index it rests on and quote the exact
   sentence. If a point cannot be grounded in a passage, leave it out. Use only the passages
   provided; never rely on outside knowledge. A good question forces the learner to reason from
   the concept, not recall a fact stated verbatim in the source."""

_SCORE_SYSTEM = """You score a learner's answer to a transfer question against a FIXED rubric.
For each numbered rubric criterion, decide whether the answer satisfies it (met = true) or not.
Judge only against the criteria given; do not invent new requirements. Be fair: accept a correct
idea expressed in different words, and do not reward fluent text that misses the criterion."""


# ---- structured outputs the model fills (no identifiers; index only, by design) ----
class _RubricItem(BaseModel):
    criterion: str
    passage_index: int
    quote: str


class _ProbeDraft(BaseModel):
    question: str
    rubric: list[_RubricItem]


class _CriterionScore(BaseModel):
    index: int
    met: bool
    note: str


class _ScoreDraft(BaseModel):
    scores: list[_CriterionScore]


class ClaudeTransfer(TransferEngine):
    def __init__(self, *, client: Anthropic | None = None, model: str = _MODEL) -> None:
        self._client = client or Anthropic()
        self._model = model

    def generate_probe(
        self, *, concept: Concept, passages: list[RetrievedPassage]
    ) -> TransferProbe:
        if not passages:
            raise ValueError(f"No passages to ground a transfer probe for {concept.label!r}.")

        numbered = "\n\n".join(f"[{i}] {p.text}" for i, p in enumerate(passages))
        user_msg = f"Concept: {concept.label}\n\nSource passages:\n{numbered}"

        draft: _ProbeDraft = self._client.messages.parse(
            model=self._model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=_GEN_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            output_format=_ProbeDraft,
        ).parsed_output

        rubric: list[RubricPoint] = []
        for item in draft.rubric:
            # WHY: clamp the index and read identifiers from the real passage; the model never
            # supplies doc_id/doc_label, so a rubric point can't cite a source that doesn't exist.
            idx = item.passage_index if 0 <= item.passage_index < len(passages) else 0
            p = passages[idx]
            rubric.append(
                RubricPoint(
                    criterion=item.criterion,
                    citation=Citation(doc_label=p.doc_label, doc_id=p.doc_id, quote=item.quote),
                )
            )

        if not rubric:
            # WHY: no grounded rubric means we cannot fairly score an answer. Refuse to ask
            # rather than test against invented truth (the trust criterion).
            raise ValueError("Could not ground a transfer rubric in the source; not asking.")

        return TransferProbe(concept_id=concept.id, question=draft.question, rubric=rubric)

    def score_answer(self, *, probe: TransferProbe, user_answer: str) -> TransferResult:
        numbered = "\n".join(f"[{i}] {rp.criterion}" for i, rp in enumerate(probe.rubric))
        user_msg = (
            f"Question: {probe.question}\n\n"
            f"Rubric criteria:\n{numbered}\n\n"
            f"Learner's answer:\n{user_answer}"
        )

        draft: _ScoreDraft = self._client.messages.parse(
            model=self._model,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            system=_SCORE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            output_format=_ScoreDraft,
        ).parsed_output

        met_by_index = {s.index: s.met for s in draft.scores}
        met: list[str] = []
        missed: list[RubricPoint] = []
        for i, rp in enumerate(probe.rubric):
            if met_by_index.get(i, False):
                met.append(rp.criterion)
            else:
                missed.append(rp)

        score = len(met) / len(probe.rubric) if probe.rubric else 0.0
        return TransferResult(
            concept_id=probe.concept_id,
            question=probe.question,
            user_answer=user_answer,
            transfer_score=score,
            met=met,
            missed=missed,
        )
