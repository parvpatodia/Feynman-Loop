#!/usr/bin/env python3
"""Claude Code Stop hook: when a session shipped a meaningful amount of AI-written code, act
according to the user's self-selected engagement mode (see feynman_loop/settings.py):

- "off"   : do nothing.
- "nudge" : queue a pending explain-back for the next SessionStart and print one transcript line.
            Never blocks. This is the default.
- "commit": a SELF-ARMED gate. Block the stop ONCE (exit code 2 feeds the message to Claude, so
            the offer is un-ignorable this session), then let go. The learner armed this with
            `feynman-loop mode commit` and can still decline; it never traps them.

Reads the per-session tally written by capture.py. Threshold = env FEYNMAN_NUDGE_LINES (default
100). Stdlib only; every error path exits 0 (a hook must never break the host), and a corrupt or
missing settings file degrades to "nudge", so a bad file can never produce a surprise block.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_MAX_PENDING = 5  # keep the pending list bounded; old unacted nudges age out

# MUST stay in sync with feynman_loop.settings (a stdlib-only hook cannot import the package).
_MODES = ("off", "nudge", "commit")
_DEFAULT_MODE = "nudge"
_SETTINGS_FILE = "feynman_settings.json"


def home_dir() -> Path:
    # WHY ".feynman-loop": must match feynman_loop.paths.home() (the reader), or the nudge is
    # written where nothing looks. Stdlib-only here, so the literal is duplicated; keep it pinned.
    return Path(os.environ.get("FEYNMAN_HOME") or str(Path.home() / ".feynman-loop"))


def scratch_path(session_id: str) -> Path:
    base = os.environ.get("FEYNMAN_SCRATCH_DIR") or tempfile.gettempdir()
    return Path(base) / f"feynman_capture_{session_id}.json"


def pending_path() -> Path:
    return home_dir() / "feynman_pending.json"


def read_mode() -> str:
    """The user's engagement mode; any problem degrades to the safe default (never a surprise gate)."""
    try:
        data = json.loads((home_dir() / _SETTINGS_FILE).read_text())
        mode = data.get("mode") if isinstance(data, dict) else None
        return mode if mode in _MODES else _DEFAULT_MODE
    except Exception:
        return _DEFAULT_MODE


def in_scope(cwd: str) -> bool:
    """Mirror of feynman_loop.settings.path_in_scope. Empty allowlist == every project; outside it
    the gate/nudge stays silent (defense in depth: capture.py also skips out-of-scope projects)."""
    try:
        data = json.loads((home_dir() / _SETTINGS_FILE).read_text())
        raw = data.get("scope") if isinstance(data, dict) else None
        allowed = [p for p in raw if isinstance(p, str)] if isinstance(raw, list) else []
    except Exception:
        allowed = []
    if not allowed or not cwd:
        return True
    c = os.path.normpath(os.path.abspath(cwd))
    return any(c == p or c.startswith(p + os.sep) for p in allowed)


def _gate_message(lines: int, files: list, target: str) -> str:
    return (
        f"feynman-loop commitment mode (the user armed this with `feynman-loop mode commit`): this "
        f"session shipped ~{lines} AI-written lines ({', '.join(files)}) with no explain-back. "
        f"Before wrapping up, OFFER one short explain-back: read {target} and pass it as source_text "
        f"to start_check, then have the user explain, in their own words, what it does and why. If "
        f"they decline or want to defer, that is allowed: acknowledge it and stop. Never answer for them."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id") or "unknown"
        spath = scratch_path(session_id)

        # WHY: Claude Code sets stop_hook_active when it is already continuing because of a prior
        # Stop-hook block. Re-blocking then would loop. Consume the tally and never block again.
        if payload.get("stop_hook_active"):
            spath.unlink(missing_ok=True)
            return 0

        if not spath.exists():
            return 0
        tally = json.loads(spath.read_text())
        spath.unlink(missing_ok=True)  # one nudge/gate per accumulation, never a loop

        if not in_scope(tally.get("cwd", "")):
            return 0  # this project is out of the user's chosen scope

        mode = read_mode()
        if mode == "off":
            return 0  # the user silenced proactivity; the tally is already cleared

        threshold = int(os.environ.get("FEYNMAN_NUDGE_LINES", "100"))
        lines = int(tally.get("lines", 0))
        if lines < threshold:
            return 0

        files = [name for name, _ in sorted(tally.get("files", {}).items(), key=lambda kv: -kv[1])[:3]]
        target = files[0] if files else "the file you shipped"

        if mode == "commit":
            # Self-armed gate: block ONCE. The tally is already cleared and stop_hook_active is
            # guarded above, so the next stop passes cleanly. Declining is a legitimate outcome
            # (it simply lapses, no nag), matching "skipping just stays due" (Principle 6).
            sys.stderr.write(_gate_message(lines, files, target))
            return 2

        # nudge: queue for the next SessionStart and leave one transcript line. Never blocks.
        item = {
            "at": datetime.now(timezone.utc).isoformat(),
            "cwd": tally.get("cwd", ""),
            "lines": lines,
            "files": files,
        }
        ppath = pending_path()
        items = []
        if ppath.exists():
            items = json.loads(ppath.read_text()).get("items", [])
        items = (items + [item])[-_MAX_PENDING:]
        ppath.parent.mkdir(parents=True, exist_ok=True)
        ppath.write_text(json.dumps({"items": items}, indent=2))

        print(
            f"feynman-loop: this session shipped ~{lines} AI-written lines "
            f"({', '.join(files)}). It is queued for an explain-back next session."
        )
    except Exception:
        # WHY exit 0 on ANY error: a hook must never break the host, and an error must never
        # produce a spurious block. The worst case is a missed nudge, never a trapped session.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
