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
from datetime import datetime, timezone
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


def derive_profile(events: list[ReviewEvent]) -> dict:
    """Aggregate the log into the learner's profile. Computed in code, deterministically, so the
    insight is auditable from the events rather than a model's impression."""
    if not events:
        return {"reviews": 0, "concepts": 0, "insight": "No reviews yet."}

    explains = [e for e in events if e.kind == "explain"]
    transfers = [e for e in events if e.kind == "transfer"]

    def _avg(xs: list[ReviewEvent]) -> float | None:
        return round(sum(x.score for x in xs) / len(xs), 2) if xs else None

    avg_explain, avg_transfer = _avg(explains), _avg(transfers)
    tag_counts = Counter(t for e in events for t in e.tags)
    weak_modes = [t for t, _ in tag_counts.most_common(3)]

    lines: list[str] = []
    if avg_explain is not None and avg_transfer is not None and avg_explain - avg_transfer >= 0.2:
        lines.append(
            f"You state concepts better than you apply them (explain {avg_explain:.0%} vs "
            f"transfer {avg_transfer:.0%}). Push for application, not restatement."
        )
    if weak_modes:
        top = weak_modes[0]
        lines.append(f"Your most frequent weak spot is {top}: {_TAG_MEANINGS.get(top, top)}.")
    if not lines:
        lines.append("No recurring failure pattern yet; keep reviewing.")

    return {
        "reviews": len(events),
        "concepts": len({e.concept_label for e in events}),
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
