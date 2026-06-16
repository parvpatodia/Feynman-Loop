"""User-selected engagement mode: the one place that decides how proactive the loop is.

The product is opt-in by design (the trust criterion: an interruption is only welcome when the
user invited it). The mode is the USER's explicit choice, never inferred or changed by the system:

- "nudge"  (default): offer an explain-back at a natural boundary (SessionStart); never forced.
- "commit" (self-armed teeth): also gate at session end. If you shipped unexplained AI-written
           code, the Stop hook asks you to explain it before wrapping up. You armed it, you can
           still decline, and it never traps you (the gate fires once, then lets go).
- "off"   : silence every proactive surface (SessionStart context + the daily notification).

Stored in $FEYNMAN_HOME/feynman_settings.json. A stdlib-only hook (stop_nudge.py) cannot import
this package, so it reads the same file directly; keep MODES / DEFAULT_MODE / SETTINGS_FILE in
sync with that copy. This is the same "two surfaces, one format, keep the literals pinned" rule
that the pending-path divergence taught us.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MODES = ("off", "nudge", "commit")
DEFAULT_MODE = "nudge"
SETTINGS_FILE = "feynman_settings.json"


def get_mode(root: Path) -> str:
    """Read the user's mode. Any corruption, missing file, or unknown value degrades to the safe
    default (nudge), so a bad settings file can never produce a surprise gate or crash a hook."""
    try:
        data = json.loads((root / SETTINGS_FILE).read_text())
        mode = data.get("mode") if isinstance(data, dict) else None
        return mode if mode in MODES else DEFAULT_MODE
    except (OSError, ValueError):  # ValueError covers json.JSONDecodeError
        return DEFAULT_MODE


def set_mode(root: Path, mode: str) -> None:
    """Persist the chosen mode atomically. Raises ValueError on an unknown mode so the CLI rejects
    it instead of writing garbage. Other keys already in the file are preserved."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose one of {', '.join(MODES)}")
    root.mkdir(parents=True, exist_ok=True)
    path = root / SETTINGS_FILE

    data: dict = {}
    try:
        existing = json.loads(path.read_text())
        if isinstance(existing, dict):
            data = existing
    except (OSError, ValueError):
        data = {}
    data["mode"] = mode

    # WHY atomic: write to a temp file in the same dir, then os.replace (atomic on POSIX). A torn
    # write during a crash can never leave a half-written settings file that reads as garbage.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)
