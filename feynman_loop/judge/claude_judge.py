"""ClaudeJudge: build a fixed rubric for a concept, then score explanations against it.

Why a fixed rubric: a holistic "understanding %" from the model is noisy and sticky, adding a
correct sentence may not move it. Instead we derive the key points a correct explanation must
cover ONCE from the source (build_rubric), then score each review against those same points. The
percentage is computed in code from per-point statuses, so it is accurate, responsive (cover a
missed point and it rises), and consistent across attempts.

Anti-gaming: gaps are returned as PROBES (questions), never the missing fact verbatim, and the
scorer does not credit near-verbatim copying of the source as understanding. The ungameable
measure of mastery stays the transfer step (applying the concept), which copying can't fake.

Grounding integrity: the model references passages by INDEX; the code maps the index to the real
doc_id/doc_label, so a rubric point can't cite a source that doesn't exist.
"""

from __future__ import annotations

from typing import Literal

from anthropic import Anthropic
from pydantic import BaseModel

from feynman_loop.judge.base import Judge
from feynman_loop.models import MODEL_FALLBACK_LABEL, Citation, Concept, Gap, GapReport, RubricPoint
from feynman_loop.providers import judge_model
from feynman_loop.retrieval.base import RetrievedPassage
from feynman_loop.verification import STATUS_VALUE, verified_status

# WHY depth shapes the RUBRIC, not the scorer: the bar lives in what a complete explanation must
# contain. The scorer stays identical; only the criteria change with the learner's stated target.
# DEPTH_RANGES is the single source for point counts; zero-key mode enforces the minimums in code.
DEPTH_RANGES = {"overview": (3, 5), "working": (4, 8), "expert": (6, 10)}

_DEPTH_SCOPES = {
    "overview": ("the big picture: what it is, why it matters, and the main moving "
                 "parts. Do not require step-by-step internals or edge cases."),
    "working": ("the essential mechanism: what it is, how it works, and what it "
                "connects to (not trivia)."),
    "expert": ("expert mastery: the precise mechanism step by step, boundary "
               "conditions, failure modes, and distinctions from neighbouring concepts."),
}


def depth_spec(depth: str) -> tuple[str, str]:
    """(count phrase, scope sentence) for a depth, defaulting to "working"."""
    lo, hi = DEPTH_RANGES.get(depth, DEPTH_RANGES["working"])
    return f"{lo} to {hi}", _DEPTH_SCOPES.get(depth, _DEPTH_SCOPES["working"])

_RUBRIC_SYSTEM = """List the key points a complete, correct explanation of the concept must
contain, based ONLY on the source passages. Each point is one checkable idea. The learner's
target depth is {depth}: cover {scope} Aim for {count} points. Ground every point in a passage:
give the passage index and an exact quote. If a point cannot be grounded in a passage, leave it
out. Use only the passages provided; never add outside knowledge."""

_SCORE_SYSTEM = """Score a learner's explanation against a FIXED list of key points.

For each numbered point, return a status:
- "met": the explanation clearly conveys this idea in the learner's OWN words.
- "partial": it touches the idea but is vague, incomplete, or mostly restates the source verbatim.
- "missed": the idea is absent.

For every "met" or "partial", set "evidence" to a VERBATIM quote from the learner's explanation
(their exact words) that shows the idea; the system verifies the quote appears in the text and
downgrades any verdict whose evidence is not found. Leave evidence empty for "missed".

Do NOT credit near-verbatim copying of the source as understanding (that is "partial" at best).
Judge ideas, not writing: explanations may be voice-typed or informal, and a correct idea in
casual words is still "met". But naming a term is not knowing it: a point is "met" only if the
mechanism or relationship behind it is actually conveyed, not just its keyword dropped in.
For every point that is not "met", write a probe: a question that prompts the learner to retrieve
the missing idea WITHOUT revealing the answer. Judge only against the listed points; be fair to a
correct idea expressed in different words."""

_RUBRIC_KNOWLEDGE_SYSTEM = """The learner gave NO source. List the key points a complete, correct
explanation of the concept must contain, from YOUR OWN GENERAL KNOWLEDGE. The learner's target
depth is {depth}: cover {scope} Aim for {count} points. For each point, put the criterion and a
brief supporting fact in "quote"; set passage_index to 0 (unused here). Only include points you
are confident are correct."""


class _RubricItem(BaseModel):
    criterion: str
    passage_index: int
    quote: str


class _RubricDraft(BaseModel):
    points: list[_RubricItem]


class _CriterionStatus(BaseModel):
    index: int
    # WHY: a Literal -> the JSON schema enum constrains the model to exactly these three values,
    # so a capitalized/padded variant can't silently miss the score lookup and count as 0.
    status: Literal["met", "partial", "missed"]
    # WHY: verbatim quote from the LEARNER's text backing the credit; verified in code
    # (verification.verified_status), so the judge cannot grant credit it cannot point to.
    evidence: str = ""
    probe: str    # a question targeting this point; shown as the gap when not fully met


class _ScoreDraft(BaseModel):
    scores: list[_CriterionStatus]


class ClaudeJudge(Judge):
    def __init__(self, *, client: Anthropic | None = None, model: str | None = None) -> None:
        self._client = client or Anthropic()
        self._model = model or judge_model()  # env-configurable cost/accuracy point

    def build_rubric(
        self, *, concept: Concept, passages: list[RetrievedPassage]
    ) -> list[RubricPoint]:
        if not passages:
            # WHY: tier-3 (Decision 15 option b, confirmed). No source -> build the rubric from the
            # model's own knowledge, flagged lower-confidence. Transfer stays the ungameable check.
            return self._build_rubric_from_knowledge(concept)

        count, scope = depth_spec(concept.depth)
        numbered = "\n\n".join(f"[{i}] {p.text}" for i, p in enumerate(passages))
        user_msg = f"Concept: {concept.label}\n\nSource passages:\n{numbered}"

        draft: _RubricDraft = self._client.messages.parse(
            model=self._model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=_RUBRIC_SYSTEM.format(depth=concept.depth, scope=scope, count=count),
            messages=[{"role": "user", "content": user_msg}],
            output_format=_RubricDraft,
        ).parsed_output

        rubric: list[RubricPoint] = []
        for item in draft.points:
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
            raise ValueError("Could not ground any rubric point in the source; cannot judge.")
        return rubric

    def _build_rubric_from_knowledge(self, concept: Concept) -> list[RubricPoint]:
        count, scope = depth_spec(concept.depth)
        draft: _RubricDraft = self._client.messages.parse(
            model=self._model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=_RUBRIC_KNOWLEDGE_SYSTEM.format(depth=concept.depth, scope=scope, count=count),
            messages=[{"role": "user", "content": f"Concept: {concept.label}"}],
            output_format=_RubricDraft,
        ).parsed_output
        rubric = [
            RubricPoint(
                criterion=it.criterion,
                citation=Citation(doc_label=MODEL_FALLBACK_LABEL, doc_id=None, quote=it.quote),
            )
            for it in draft.points
        ]
        if not rubric:
            raise ValueError(f"Could not build a knowledge rubric for {concept.label!r}.")
        return rubric

    def evaluate(self, *, concept: Concept, user_explanation: str) -> GapReport:
        rubric = concept.rubric
        if not rubric:
            raise ValueError("Concept has no rubric; call build_rubric at setup before judging.")

        numbered = "\n".join(f"[{i}] {rp.criterion}" for i, rp in enumerate(rubric))
        user_msg = (
            f"Concept: {concept.label}\n\n"
            f"Key points a correct explanation must cover:\n{numbered}\n\n"
            f"Learner's explanation:\n{user_explanation}"
        )

        draft: _ScoreDraft = self._client.messages.parse(
            model=self._model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=_SCORE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            output_format=_ScoreDraft,
        ).parsed_output

        status_by_index = {s.index: s for s in draft.scores}
        correct_points: list[str] = []
        gaps: list[Gap] = []
        total = 0.0
        evidence_failures = 0
        for i, rp in enumerate(rubric):
            s = status_by_index.get(i)
            # WHY verified, not trusted: every credited point must carry a verbatim quote from the
            # learner's text, checked in code. Credit the judge cannot point to is downgraded.
            status, ok = verified_status(
                status=s.status if s else "missed",
                evidence=s.evidence if s else "",
                text=user_explanation,
            )
            if not ok:
                evidence_failures += 1
            value = STATUS_VALUE[status]
            total += value
            if value >= 1.0:
                correct_points.append(rp.criterion)
            else:
                # WHY: the gap is a probe (a question), never the missing fact verbatim, so copying
                # feedback back doesn't satisfy the criterion. Citation kept for audit, not displayed.
                # Fallbacks never name the criterion; that would hand the learner the point.
                if s and s.probe:
                    probe = s.probe
                elif not ok and s and s.status == "met":
                    probe = ("This was almost credited, but your explanation didn't clearly "
                             "contain it. State that part explicitly, in your own words.")
                else:
                    probe = "One required point is missing. What else would a complete explanation cover?"
                gaps.append(Gap(description=probe, citation=rp.citation))

        # WHY: understanding is computed in code from per-point statuses, not a holistic guess by
        # the model, so it is accurate and moves predictably as the learner covers more points.
        understanding_level = total / len(rubric)
        return GapReport(
            concept_id=concept.id,
            user_explanation=user_explanation,
            understanding_level=understanding_level,
            correct_points=correct_points,
            gaps=gaps,
            evidence_failures=evidence_failures,
        )
