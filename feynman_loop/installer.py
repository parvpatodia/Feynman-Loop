"""`feynman-loop init`: one command from install to working, because friction is where adoption
dies. Copies the hook scripts into the data home, merges the MCP server into Claude Desktop's
config, merges the three hooks into Claude Code's user settings, and prints the snippet for any
other MCP host (ChatGPT, Gemini, Cursor). Merges are non-destructive and idempotent: existing
entries are preserved, and a second init never double-installs.
"""

from __future__ import annotations

import json
import shutil
import sys
from importlib import resources
from pathlib import Path

from feynman_loop import paths


def copy_hook_assets(home: Path) -> Path:
    hooks_dir = home / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for name in ("capture.py", "stop_nudge.py"):
        src = resources.files("feynman_loop.assets").joinpath(f"hooks/{name}")
        with resources.as_file(src) as p:
            shutil.copy(p, hooks_dir / name)
    return hooks_dir


def merge_mcp_into_desktop_config(config_path: Path, *, python: str, home: Path, api_key: str) -> bool:
    """Add/replace the feynman-loop server entry; everything else is preserved."""
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    servers = config.setdefault("mcpServers", {})
    servers["feynman-loop"] = {
        "command": python,
        "args": ["-m", "feynman_loop.mcp_server"],
        "env": {"ANTHROPIC_API_KEY": api_key, "FEYNMAN_HOME": str(home)},
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2))
    return True


def merge_hooks_into_settings(settings_path: Path, *, python: str, home: Path) -> bool:
    """Add the three Claude Code hooks; returns False (no-op) if feynman hooks already exist."""
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    if "feynman" in json.dumps(hooks):
        return False
    hooks.setdefault("SessionStart", []).append({"hooks": [{"type": "command",
        "command": f"FEYNMAN_HOME={home} {python} -m feynman_loop.due --context --quiet"}]})
    hooks.setdefault("PostToolUse", []).append({"matcher": "Edit|Write|MultiEdit", "hooks": [{
        "type": "command", "command": f"FEYNMAN_SCRATCH_DIR={home} {python} {home / 'hooks' / 'capture.py'}"}]})
    hooks.setdefault("Stop", []).append({"hooks": [{"type": "command",
        "command": f"FEYNMAN_HOME={home} FEYNMAN_SCRATCH_DIR={home} {python} {home / 'hooks' / 'stop_nudge.py'}"}]})
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2))
    return True


def generic_mcp_snippet(*, python: str, home: Path) -> str:
    return json.dumps({
        "feynman-loop": {
            "command": python,
            "args": ["-m", "feynman_loop.mcp_server"],
            "env": {"ANTHROPIC_API_KEY": "<your key>", "FEYNMAN_HOME": str(home)},
        }
    }, indent=2)


def run_init(*, api_key: str | None = None) -> int:
    import os

    home = paths.home()
    python = sys.executable
    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or "REPLACE_WITH_YOUR_ANTHROPIC_KEY"

    hooks_dir = copy_hook_assets(home)
    print(f"ledger home : {home}")
    print(f"hook scripts: {hooks_dir}")

    desktop = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if desktop.parent.exists():
        merge_mcp_into_desktop_config(desktop, python=python, home=home, api_key=key)
        print(f"Claude Desktop: feynman-loop server configured ({desktop}). Restart Claude Desktop.")
    else:
        print("Claude Desktop not found; skipped.")

    settings = Path.home() / ".claude/settings.json"
    if settings.parent.exists():
        added = merge_hooks_into_settings(settings, python=python, home=home)
        print("Claude Code hooks: " + ("installed (next session)." if added else "already present."))
    else:
        print("Claude Code (~/.claude) not found; hooks skipped.")

    if key == "REPLACE_WITH_YOUR_ANTHROPIC_KEY":
        print("\nNOTE: no ANTHROPIC_API_KEY found; edit the Desktop config and set your key.")
    print("\nFor any other MCP host (ChatGPT Desktop, Gemini, Cursor), add:\n"
          + generic_mcp_snippet(python=python, home=home))
    return 0
