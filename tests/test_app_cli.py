"""Console-command tests for `feynman-loop mode` (show / set / reject), driven through the real
argparse dispatch with FEYNMAN_HOME pointed at a tmp dir."""

from feynman_loop import settings
from feynman_loop.app_cli import main


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
