from datetime import datetime, timezone

from feynman_loop.scheduling import compute_next_due

_NOW = datetime(2026, 6, 9, tzinfo=timezone.utc)


def test_low_understanding_comes_back_soon():
    due = compute_next_due(0.0, now=_NOW)
    assert (due - _NOW).days == 1


def test_high_understanding_comes_back_far_out():
    due = compute_next_due(1.0, now=_NOW)
    assert (due - _NOW).days == 30


def test_more_understanding_means_later_due():
    low = compute_next_due(0.2, now=_NOW)
    high = compute_next_due(0.8, now=_NOW)
    assert high > low


def test_out_of_range_is_clamped():
    assert compute_next_due(-5.0, now=_NOW) == compute_next_due(0.0, now=_NOW)
    assert compute_next_due(9.0, now=_NOW) == compute_next_due(1.0, now=_NOW)
