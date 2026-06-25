"""Console-command tests for `feynman-loop mode` (show / set / reject), driven through the real
argparse dispatch with FEYNMAN_HOME pointed at a tmp dir."""

import os

from feynman_loop import settings
from feynman_loop.app_cli import main


def _put_concept(root, label, project=None):
    from feynman_loop.db import stores_for
    from feynman_loop.models import Concept, SourceRef, SourceTier

    stores_for(root).concepts.put(Concept(
        label=label, project=project,
        source_ref=SourceRef(tier=SourceTier.MODEL_FALLBACK,
                             doc_label="general knowledge (unverified)", retrieval_query=label)))


def _concept(root, label):
    from feynman_loop.db import stores_for
    return stores_for(root).concepts.find_by_label(label)


def test_mode_set_and_show(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FEYNMAN_HOME", str(tmp_path))

    assert main(["mode", "commit"]) == 0
    assert settings.get_mode(tmp_path) == "commit"          # persisted
    assert "Self-armed gate ON" in capsys.readouterr().out  # honest description of the choice

    assert main(["mode"]) == 0                              # show current
    assert capsys.readouterr().out.strip() == "commit"


def test_mode_rejects_unknown_without_writing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FEYNMAN_HOME", str(tmp_path))
    assert main(["mode", "aggressive"]) == 2                # non-zero exit, not silently ok
    assert "unknown mode" in capsys.readouterr().out
    assert settings.get_mode(tmp_path) == "nudge"           # nothing written -> safe default


def test_scope_add_show_and_reset(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FEYNMAN_HOME", str(tmp_path))

    assert main(["scope"]) == 0
    assert "all projects" in capsys.readouterr().out        # default: everywhere

    assert main(["scope", "add", "/Users/x/study"]) == 0
    assert settings.get_scope(tmp_path) == ["/Users/x/study"]
    capsys.readouterr()

    assert main(["scope"]) == 0
    assert "/Users/x/study" in capsys.readouterr().out      # shows the allowlist

    assert main(["scope", "all"]) == 0
    assert settings.get_scope(tmp_path) == []               # reset to everywhere


def test_scope_add_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("FEYNMAN_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert main(["scope", "add"]) == 0                       # no PATH -> current directory
    assert settings.get_scope(tmp_path) == [settings._norm(str(tmp_path))]


def test_projects_lists_concepts_grouped_by_bucket(tmp_path, monkeypatch, capsys):
    """`feynman-loop projects` is the read-only audit of project-scoped recall: it shows each
    project's concepts and the global bucket, so the user can verify what is filed where."""
    monkeypatch.setenv("FEYNMAN_HOME", str(tmp_path))
    _put_concept(tmp_path, "Diffusion Policy", project="/repo/av")
    _put_concept(tmp_path, "Gradient Descent", project=None)

    assert main(["projects"]) == 0
    out = capsys.readouterr().out
    assert "/repo/av" in out and "Diffusion Policy" in out          # named project + its concept
    assert "global / uncategorized" in out and "Gradient Descent" in out  # the global bucket


def test_reproject_moves_concept_to_a_project_then_back_to_global(tmp_path, monkeypatch, capsys):
    """The explicit re-tag escape hatch: name a concept and a project and it moves; --global clears
    it. This is how an existing global concept gets filed under a project after the fact."""
    monkeypatch.setenv("FEYNMAN_HOME", str(tmp_path))
    monkeypatch.setattr(settings, "_git_root", lambda cwd: None)   # project == normalized path
    _put_concept(tmp_path, "Gradient Descent", project=None)       # starts global

    assert main(["reproject", "gradient descent", "/repo/kd"]) == 0   # label match is case-insensitive
    assert _concept(tmp_path, "Gradient Descent").project == settings.project_for("/repo/kd")
    assert "reprojected" in capsys.readouterr().out

    assert main(["reproject", "Gradient Descent", "--global"]) == 0   # clear the tag
    assert _concept(tmp_path, "Gradient Descent").project is None


def test_reproject_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("FEYNMAN_HOME", str(tmp_path))
    monkeypatch.setattr(settings, "_git_root", lambda cwd: None)
    monkeypatch.chdir(tmp_path)
    _put_concept(tmp_path, "Osmosis", project=None)
    assert main(["reproject", "Osmosis"]) == 0                      # no PATH -> current directory
    assert _concept(tmp_path, "Osmosis").project == settings.project_for(os.getcwd())


def test_reproject_unknown_concept_errors_without_change(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FEYNMAN_HOME", str(tmp_path))
    _put_concept(tmp_path, "Osmosis", project=None)
    assert main(["reproject", "Nonexistent", "/repo/x"]) == 2       # non-zero exit, not silently ok
    assert "no concept matches" in capsys.readouterr().out
    assert _concept(tmp_path, "Osmosis").project is None            # nothing else touched
