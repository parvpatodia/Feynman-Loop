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


def test_scope_defaults_to_everywhere(tmp_path):
    assert settings.get_scope(tmp_path) == []                       # empty == all projects
    assert settings.path_in_scope("/any/where", []) is True
    assert settings.path_in_scope(None, []) is True


def test_scope_add_remove_and_matching(tmp_path):
    settings.add_scope_path(tmp_path, "/Users/x/study")
    settings.add_scope_path(tmp_path, "/Users/x/study")            # idempotent
    settings.add_scope_path(tmp_path, "/Users/x/papers")
    assert settings.get_scope(tmp_path) == ["/Users/x/study", "/Users/x/papers"]

    allowed = settings.get_scope(tmp_path)
    assert settings.path_in_scope("/Users/x/study", allowed) is True        # exact dir
    assert settings.path_in_scope("/Users/x/study/sub/deep", allowed) is True  # nested
    assert settings.path_in_scope("/Users/x/work", allowed) is False        # outside
    assert settings.path_in_scope("/Users/x/study-notes", allowed) is False  # prefix is not a parent

    settings.remove_scope_path(tmp_path, "/Users/x/study")
    assert settings.get_scope(tmp_path) == ["/Users/x/papers"]
    settings.set_scope_all(tmp_path)
    assert settings.get_scope(tmp_path) == []                       # back to everywhere


def test_scope_and_mode_coexist(tmp_path):
    settings.set_mode(tmp_path, "commit")
    settings.add_scope_path(tmp_path, "/Users/x/study")
    assert settings.get_mode(tmp_path) == "commit"                  # neither clobbers the other
    assert settings.get_scope(tmp_path) == ["/Users/x/study"]


# --- project identity ---

def test_project_for_none_when_cwd_unknown():
    assert settings.project_for(None) is None      # unknown cwd -> no project (callers fail open)
    assert settings.project_for("") is None


def test_project_for_uses_git_root_collapsing_subdirs(monkeypatch):
    # a sub-directory of a repo resolves to the SAME project as the repo root: one repo, one bucket
    monkeypatch.setattr(settings, "_git_root", lambda cwd: "/Users/x/repo")
    assert settings.project_for("/Users/x/repo") == "/Users/x/repo"
    assert settings.project_for("/Users/x/repo/pkg/sub") == "/Users/x/repo"


def test_project_for_falls_back_to_cwd_outside_a_repo(monkeypatch):
    monkeypatch.setattr(settings, "_git_root", lambda cwd: None)   # not a repo / no git
    assert settings.project_for("/tmp/loose/dir") == "/tmp/loose/dir"
    # the fallback is normalized, like scope paths, so it matches what `due` filters on
    assert settings.project_for("/tmp/loose/../loose/dir") == "/tmp/loose/dir"


def test_git_root_returns_repo_toplevel(tmp_path):
    """Real git: a sub-directory of a repo reports the repo root; both due and the server agree."""
    import shutil
    import subprocess
    if shutil.which("git") is None:
        pytest.skip("git not available")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sub = tmp_path / "pkg" / "deep"
    sub.mkdir(parents=True)
    # macOS /private symlink and the like: compare against git's own normalized answer
    expected = settings._norm(str(tmp_path.resolve()))
    assert settings.project_for(str(sub)) == expected


def test_git_root_failsafe_when_git_unavailable(monkeypatch):
    def _boom(*a, **k):
        raise OSError("git not found")
    monkeypatch.setattr(settings.subprocess, "run", _boom)
    assert settings._git_root("/anywhere") is None         # never raises into a hook/server


def test_concept_in_project_rules():
    p = "/Users/x/repo"
    assert settings.concept_in_project(None, p) is True        # global surfaces in any project
    assert settings.concept_in_project(None, None) is True     # global, unknown session
    assert settings.concept_in_project(p, None) is True        # unknown session -> fail open
    assert settings.concept_in_project(p, p) is True           # same project
    assert settings.concept_in_project(p, "/Users/x/other") is False  # different project: hidden
