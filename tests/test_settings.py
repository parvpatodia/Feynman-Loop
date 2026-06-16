"""Engagement-mode settings: round-trip, validation, safe degradation, atomic write."""

import json

import pytest

from feynman_loop import settings


def test_default_mode_when_no_file(tmp_path):
    assert settings.get_mode(tmp_path) == "nudge"  # opt-in by default, never a surprise gate


def test_set_then_get_round_trips(tmp_path):
    for mode in ("off", "commit", "nudge"):
        settings.set_mode(tmp_path, mode)
        assert settings.get_mode(tmp_path) == mode


def test_unknown_mode_is_rejected_not_written(tmp_path):
    with pytest.raises(ValueError):
        settings.set_mode(tmp_path, "aggressive")
    assert not (tmp_path / settings.SETTINGS_FILE).exists()  # garbage never lands on disk


def test_corrupt_or_unknown_value_degrades_to_default(tmp_path):
    (tmp_path / settings.SETTINGS_FILE).write_text("{not json")
    assert settings.get_mode(tmp_path) == "nudge"          # corruption -> safe default
    (tmp_path / settings.SETTINGS_FILE).write_text(json.dumps({"mode": "evil"}))
    assert settings.get_mode(tmp_path) == "nudge"          # unknown value -> safe default


def test_set_mode_preserves_other_keys(tmp_path):
    (tmp_path / settings.SETTINGS_FILE).write_text(json.dumps({"other": 1}))
    settings.set_mode(tmp_path, "commit")
    data = json.loads((tmp_path / settings.SETTINGS_FILE).read_text())
    assert data == {"other": 1, "mode": "commit"}          # unrelated settings survive
