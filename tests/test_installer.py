"""Installer merge tests: non-destructive, idempotent config merging."""

import json

from feynman_loop.installer import (
    copy_hook_assets,
    generic_mcp_snippet,
    merge_hooks_into_settings,
    merge_mcp_into_desktop_config,
)


def test_desktop_merge_preserves_existing_servers(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"pencil": {"command": "x"}}, "other": 1}))
    merge_mcp_into_desktop_config(cfg, python="/venv/python", home=tmp_path, api_key="k")
    out = json.loads(cfg.read_text())
    assert out["mcpServers"]["pencil"] == {"command": "x"}      # untouched
    assert out["other"] == 1                                     # untouched
    fl = out["mcpServers"]["feynman-loop"]
    assert fl["args"] == ["-m", "feynman_loop.mcp_server"]
    assert fl["env"]["FEYNMAN_HOME"] == str(tmp_path)


def test_desktop_merge_creates_fresh_config(tmp_path):
    cfg = tmp_path / "sub" / "claude_desktop_config.json"
    merge_mcp_into_desktop_config(cfg, python="p", home=tmp_path, api_key="k")
    assert "feynman-loop" in json.loads(cfg.read_text())["mcpServers"]


def test_hooks_merge_is_idempotent_and_preserving(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
                                    "model": "opus"}))
    assert merge_hooks_into_settings(settings, python="py", home=tmp_path) is True
    out = json.loads(settings.read_text())
    assert out["model"] == "opus"
    assert len(out["hooks"]["Stop"]) == 2          # existing + nudge
    assert len(out["hooks"]["SessionStart"]) == 1
    assert out["hooks"]["PostToolUse"][0]["matcher"] == "Edit|Write|MultiEdit"
    # second run: no double-install
    assert merge_hooks_into_settings(settings, python="py", home=tmp_path) is False
    assert len(json.loads(settings.read_text())["hooks"]["Stop"]) == 2


def test_copy_hook_assets(tmp_path):
    hooks_dir = copy_hook_assets(tmp_path)
    assert (hooks_dir / "capture.py").exists()
    assert (hooks_dir / "stop_nudge.py").exists()
    assert "stdin" in (hooks_dir / "capture.py").read_text().lower() or \
           "session" in (hooks_dir / "capture.py").read_text().lower()


def test_generic_snippet_is_valid_json(tmp_path):
    snippet = json.loads(generic_mcp_snippet(python="p", home=tmp_path))
    assert snippet["feynman-loop"]["args"] == ["-m", "feynman_loop.mcp_server"]


def test_notification_agent_plist_runs_due_notify(tmp_path):
    from feynman_loop.installer import notification_agent_plist

    plist = notification_agent_plist(python="/venv/python", home=tmp_path, hour=9)
    assert "<string>-m</string>" in plist and "<string>feynman_loop.due</string>" in plist
    assert "<string>--notify</string>" in plist
    assert f"<string>{tmp_path}</string>" in plist     # FEYNMAN_HOME pinned for launchd
    assert "<integer>9</integer>" in plist             # fires at the chosen hour
