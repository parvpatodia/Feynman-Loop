"""The learner ledger: a durable, structured record of what the user can and cannot explain.

This is the part a prompt cannot fake and Claude's own memory does not build. Claude's memory
learns your style and preferences (a personality model); this accumulates scored evidence of your
understanding across concepts and months (a competence model): every review and transfer outcome,
what was missed, and which KIND of understanding keeps failing. `derive_profile` turns the raw
events into the meta-insight no single chat can see ("you state concepts well but can't apply
them"; "your recurring weak spot is mechanism").
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from anthropic import Anthropic
from pydantic import BaseModel, Field

# The failure-mode taxonomy. Small on purpose: tags are only useful if they recur.
MissTag = Literal["mechanism", "purpose", "structure", "application", "distinction", "context"]

_TAG_MEANINGS = {
    "mechanism": "how things actually work, step by step",
    "purpose": "why something exists / what it is for",
    "structure": "the parts and how they are organized",
    "application": "using the idea on cases you were not shown",
    "distinction": "boundaries between the idea and its neighbours",
    "context": "history, naming, and where it fits in the bigger picture",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReviewEvent(BaseModel):
    """One scored attempt: an explanation review or a transfer answer."""

    concept_id: UUID
    concept_label: str
    kind: Literal["explain", "transfer"]
    score: float
    missed: list[str] = Field(default_factory=list)  # criteria not fully met, verbatim
    tags: list[str] = Field(default_factory=list)    # failure-mode tags for the misses
    # WHY: the user's own words, kept verbatim. The journey from 0 to 90 only becomes visible
    # (and joyful) if the person can read what they said three weeks ago next to what they say
    # today. Stored locally; this is the user's growth record, not judge input.
    explanation: str = ""
    rehearsed: bool = False  # near-verbatim repeat of the prior attempt (soft signal, no penalty)
    # WHY: provenance. "independent" = our API judge scored it; "host" = the user's own host model
    # under the verified protocol (evidence checked in code, score computed in code, but softer:
    # the host can be lenient within its quotes). The ledger stays honest about its own strength.
    judge: Literal["independent", "host"] = "independent"
    # WHY: how the explanation arrived. "full" = one composed explanation; "rapid" = a volley of
    # per-point one-liners. Same scoring math, but the record keeps the distinction honest
    # (synthesis is only proven by full explanations and transfer).
    mode: Literal["full", "rapid"] = "full"
    at: datetime = Field(default_factory=_utcnow)


class JsonLearnerLog:
    """Append-only event log. Events are never rewritten; the profile is derived on read."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append(self, event: ReviewEvent) -> None:
        events = self._raw()
        events.append(event.model_dump(mode="json"))
        self._path.write_text(json.dumps(events, indent=2, default=str))

    def events(self) -> list[ReviewEvent]:
        return [ReviewEvent.model_validate(e) for e in self._raw()]

    def _raw(self) -> list[dict]:
        return json.loads(self._path.read_text()) if self._path.exists() else []


def streak_days(events: list[ReviewEvent], *, now: datetime | None = None) -> int:
    """Consecutive days with at least one rep, counting back from today (yesterday keeps it
    alive). WHY this is the ONE gamified number: it rewards showing up, which is the behavior
    that compounds; ranking scores would reward gaming them (Decision 8).

    WHY local dates, not UTC: a streak is a human-day concept. On UTC days, a US-evening user
    repping nightly at 7pm (2am UTC) would see streaks break or double-count at the UTC
    boundary. Timestamps stay UTC in the ledger; only the day bucketing is local."""
    now = now or _utcnow()
    days = {e.at.astimezone().date() for e in events}
    day = now.astimezone().date()
    if day not in days:
        day = day - timedelta(days=1)  # today's rep not done yet; streak survives until midnight
        if day not in days:
            return 0
    n = 0
    while day in days:
        n += 1
        day = day - timedelta(days=1)
    return n


# Milestones are a PURE function of the event log: no award table to store, nothing to game.
# They reward showing up and coming back, never the score (Decision 8). Streak badges re-arm
# after a lapse on purpose: re-earning "3-day streak" is exactly the moment to celebrate.
_STREAK_MILESTONES = ((30, "30-day streak"), (14, "14-day streak"), (7, "7-day streak"),
                      (3, "3-day streak"))
_TRANSFER_PASS = 0.6  # mirrors loop.TRANSFER_GATE; local to avoid an import cycle


def unlocked_milestones(events: list[ReviewEvent], *, now: datetime | None = None) -> list[str]:
    """Every milestone the history implies right now. Callers diff against the log minus its
    newest event to find what THIS rep unlocked."""
    if not events:
        return []
    out: list[str] = []
    if any(e.kind == "explain" for e in events):
        out.append("First check complete")
    if any(e.kind == "transfer" and e.score >= _TRANSFER_PASS for e in events):
        out.append("First transfer passed")
    labels = len({e.concept_label for e in events})
    for n in (25, 10, 5):
        if labels >= n:
            out.append(f"{n} concepts tracked")
            break
    s = streak_days(events, now=now)
    for d, name in _STREAK_MILESTONES:
        if s >= d:
            out.append(name)
            break
    if any((b.at - a.at).days >= 7 for a, b in zip(events, events[1:], strict=False)):
        out.append("Comeback: back after a break")
    return out


def derive_profile(events: list[ReviewEvent], *, now: datetime | None = None) -> dict:
    """Aggregate the log into the learner's profile. Computed in code, deterministically, so the
    insight is auditable from the events rather than a model's impression."""
    if not events:
        return {"reviews": 0, "concepts": 0, "streak_days": 0, "insight": "No reviews yet."}

    explains = [e for e in events if e.kind == "explain"]
    transfers = [e for e in events if e.kind == "transfer"]

    def _avg(xs: list[ReviewEvent]) -> float | None:
        return round(sum(x.score for x in xs) / len(xs), 2) if xs else None

    avg_explain, avg_transfer = _avg(explains), _avg(transfers)
    tag_counts = Counter(t for e in events for t in e.tags)
    weak_modes = [t for t, _ in tag_counts.most_common(3)]

    lines: list[str] = []
    # WHY: "you state better than you apply" is only honest as a WITHIN-concept comparison.
    # Pooling an explain of concept A against a transfer of concept B manufactures a false
    # "you can't apply" from apples-to-oranges evidence — a lie in the competence model the moat
    # rests on (Principle 4: measure understanding, not activity) and a trust breach (one false
    # "you're wrong" is unrecoverable). Restrict BOTH sides to concepts the learner did both on,
    # so the claim and its numbers are auditable from the events. Group by label, like the
    # concept count above. Empty intersection -> no within-concept evidence -> no claim.
    paired = {e.concept_label for e in explains} & {e.concept_label for e in transfers}
    pe = _avg([e for e in explains if e.concept_label in paired])
    pt = _avg([e for e in transfers if e.concept_label in paired])
    if pe is not None and pt is not None and pe - pt >= 0.2:
        lines.append(
            f"You state concepts better than you apply them (explain {pe:.0%} vs "
            f"transfer {pt:.0%}). Push for application, not restatement."
        )
    if weak_modes:
        top = weak_modes[0]
        lines.append(f"Your most frequent weak spot is {top}: {_TAG_MEANINGS.get(top, top)}.")
    if not lines:
        lines.append("No recurring failure pattern yet; keep reviewing.")

    return {
        "reviews": len(events),
        "concepts": len({e.concept_label for e in events}),
        "streak_days": streak_days(events, now=now),
        "avg_explain": avg_explain,
        "avg_transfer": avg_transfer,
        "weak_modes": weak_modes,
        "insight": " ".join(lines),
    }


# --- failure-mode tagging (Haiku: trivial, latency-sensitive subtask) ---

_TAG_SYSTEM = """Classify each numbered missed-criterion into exactly one failure-mode tag:
mechanism (how it works), purpose (why/what for), structure (parts/organization),
application (using it on new cases), distinction (boundaries vs related ideas),
context (history/naming/bigger picture). Return one tag per item, in order."""


class _TagDraft(BaseModel):
    tags: list[MissTag]


class ClaudeMissTagger:
    def __init__(self, *, client: Anthropic | None = None, model: str | None = None) -> None:
        from feynman_loop.providers import fast_model  # local import: avoid cycle at module load

        self._client = client or Anthropic()
        self._model = model or fast_model()

    def tag(self, missed: list[str]) -> list[str]:
        if not missed:
            return []
        numbered = "\n".join(f"[{i}] {m}" for i, m in enumerate(missed))
        draft: _TagDraft = self._client.messages.parse(
            model=self._model,
            max_tokens=512,
            system=_TAG_SYSTEM,
            messages=[{"role": "user", "content": numbered}],
            output_format=_TagDraft,
        ).parsed_output
        # pair by index; a short/long answer never corrupts the log
        return list(draft.tags[: len(missed)])
