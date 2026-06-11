"""The hybrid due-interval (Decision 10).

`next_due_at` is computed here, from the review's understanding_level. A clean explanation
pushes the next review further out; a weak one brings it back soon. The interval is recomputed
each review from understanding_level alone, so there is no stored ease_factor (the open DESIGN
NOTE in user_state.py). Bounded so it never collapses to 0 or runs away.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_MIN_DAYS = 1.0    # even a perfect explanation comes back eventually (forgetting curve)
_MAX_DAYS = 30.0   # a total blank comes back tomorrow, not never


def compute_next_due(understanding_level: float, *, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    u = max(0.0, min(1.0, understanding_level))  # clamp into [0, 1]
    # WHY: linear blend. u=0 -> 1 day (relearn soon), u=1 -> 30 days (you know it, leave it).
    # Both forces are present: time always pulls it back, understanding sets how soon.
    days = _MIN_DAYS + (_MAX_DAYS - _MIN_DAYS) * u
    return now + timedelta(days=days)


def gated_next_due(
    understanding_level: float,
    *,
    now: datetime,
    prior_last_reviewed: datetime | None,
    prior_next_due: datetime | None,
    rehearsed: bool = False,
) -> datetime:
    """The echo fix: interval GROWTH must be earned by elapsed time.

    Re-explaining minutes after reading the feedback proves short-term echo, not retention, so
    an early review only extends the interval in proportion to how much of the prior interval
    actually passed. Shrinkage (a low score) applies immediately: failure is real evidence
    regardless of timing. A first review (no prior schedule) gets the full score-based interval.
    A rehearsed attempt (near-verbatim repeat) never grows the interval at all."""
    candidate = compute_next_due(understanding_level, now=now) - now

    if prior_last_reviewed is None or prior_next_due is None:
        return now + candidate
    prior_interval = prior_next_due - prior_last_reviewed
    if prior_interval.total_seconds() <= 0:
        return now + candidate

    if candidate <= prior_interval:  # shrink: immediate
        return now + candidate
    if rehearsed:                    # rehearsal: hold the line, never extend
        return now + prior_interval

    elapsed = now - prior_last_reviewed
    earned = max(0.0, min(1.0, elapsed / prior_interval))
    allowed = prior_interval + (candidate - prior_interval) * earned
    return now + allowed
