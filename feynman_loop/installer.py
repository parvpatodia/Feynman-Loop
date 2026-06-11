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


def merge_mcp_into_desktop_config(config_path: Path, *, python: str, home: Path,
                                  api_key: str | None) -> bool:
    """Add/replace the feynman-loop server entry; everything else is preserved.
    WHY the key is optional: without one, the server runs in zero-key mode (the host model
    judges under the verified protocol), so setup works with just the LLM the user already has."""
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    servers = config.setdefault("mcpServers", {})
    env = {"FEYNMAN_HOME": str(home)}
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    servers["feynman-loop"] = {
        "command": python,
        "args": ["-m", "feynman_loop.mcp_server"],
        "env": env,
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


def notification_agent_plist(*, python: str, home: Path, hour: int = 10) -> str:
    """A launchd agent that posts the daily due-question notification. OPT-IN only: init never
    installs a background job silently; the user passes --notifications to ask for it."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.feynman-loop.due</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>feynman_loop.due</string>
    <string>--notify</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>FEYNMAN_HOME</key><string>{home}</string></dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>0</integer></dict>
</dict>
</plist>
"""


def install_notification_agent(*, python: str, home: Path, hour: int = 10) -> Path:
    import subprocess

    agents = Path.home() / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    plist = agents / "com.feynman-loop.due.plist"
    plist.write_text(notification_agent_plist(python=python, home=home, hour=hour))
    subprocess.run(["launchctl", "unload", str(plist)], check=False, capture_output=True)
    subprocess.run(["launchctl", "load", str(plist)], check=False, capture_output=True)
    return plist


def generic_mcp_snippet(*, python: str, home: Path) -> str:
    return json.dumps({
        "feynman-loop": {
            "command": python,
            "args": ["-m", "feynman_loop.mcp_server"],
            "env": {"FEYNMAN_HOME": str(home)},
        }
    }, indent=2)


def run_init(*, api_key: str | None = None, notifications: bool = False) -> int:
    import os
    import platform

    home = paths.home()
    python = sys.executable
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")  # optional: zero-key mode without it

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

    if notifications:
        if platform.system() == "Darwin":
            plist = install_notification_agent(python=python, home=home)
            print(f"Daily due-question notification: 10:00 ({plist}).")
        else:
            print("Notifications agent is macOS-only for now; run "
                  "`feynman-loop due --notify` from cron instead.")
    else:
        print("Daily due-question notification: off. Enable with `feynman-loop init --notifications`.")

    if not key:
        print("\nNo API key found: zero-key mode. Your own chat model judges under a verified "
              "protocol\n(evidence checked in code, scores computed in code). For the strongest "
              "judging,\nadd an ANTHROPIC_API_KEY later: feynman-loop init --key sk-ant-...")

    # Obsidian: detect and guide; never auto-install third-party software (the security-conscious
    # users this serves would rightly uninstall a tool that does).
    vault = home / "vault"
    if Path("/Applications/Obsidian.app").exists() or shutil.which("obsidian"):
        print(f"\nObsidian detected: open this folder as a vault to see your knowledge graph:\n  {vault}")
    else:
        print(f"\nKnowledge graph: plain markdown at {vault} (works as-is)."
              "\nFor the interactive graph view, install Obsidian (https://obsidian.md) and open that folder as a vault.")

    # Pre-download the local embedding model so the first long-document check doesn't hang.
    # Normal pasted sources never touch embeddings (direct grounding), so missing extras is fine.
    try:
        from feynman_loop.retrieval.chroma_store import sentence_transformer_embedder
    except ImportError:
        print("\nEmbeddings extra not installed; pasted sources still work fully. For long-"
              'document grounding and the web UI: pip install "feynman-loop[embeddings]"')
    else:
        print("\nWarming the local embedding model (~80MB, one-time)...")
        try:
            sentence_transformer_embedder()(["warm up"])
            print("Embedding model ready.")
        except Exception:
            print("Could not pre-download (offline?); it will download on first long-document check.")

    print("\nFor any other MCP host (ChatGPT Desktop, Gemini, Cursor), add:\n"
          + generic_mcp_snippet(python=python, home=home))
    return 0
