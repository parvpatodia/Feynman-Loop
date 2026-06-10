#!/usr/bin/env python3
"""Claude Code Stop hook: if this session shipped a meaningful amount of AI-written code,
record a pending explain-back nudge.

Reads the per-session tally written by capture.py. At or above the threshold (env
FEYNMAN_NUDGE_LINES, default 100) it appends an item to $FEYNMAN_HOME/feynman_pending.json,
which the next session's SessionStart hook (feynman_loop.due --context) surfaces as: "you
shipped ~N lines in <project> without an explain-back". It also prints one line to stdout for
the transcript. It never blocks the host and never repeats: the scratch tally is cleared.

Stdlib only; every failure exits 0.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_MAX_PENDING = 5  # keep the pending list bounded; old unacted nudges age out


def scratch_path(session_id: str) -> Path:
    base = os.environ.get("FEYNMAN_SCRATCH_DIR") or tempfile.gettempdir()
    return Path(base) / f"feynman_capture_{session_id}.json"


def pending_path() -> Path:
    home = os.environ.get("FEYNMAN_HOME") or str(Path.home() / "Feynman-Loop")
    return Path(home) / "feynman_pending.json"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id") or "unknown"
        spath = scratch_path(session_id)
        if not spath.exists():
            return 0
        tally = json.loads(spath.read_text())
        spath.unlink(missing_ok=True)  # one nudge per accumulation, never a loop

        threshold = int(os.environ.get("FEYNMAN_NUDGE_LINES", "100"))
        lines = int(tally.get("lines", 0))
        if lines < threshold:
            return 0

        files = sorted(tally.get("files", {}).items(), key=lambda kv: -kv[1])
        item = {
            "at": datetime.now(timezone.utc).isoformat(),
            "cwd": tally.get("cwd", ""),
            "lines": lines,
            "files": [name for name, _ in files[:3]],
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
            f"({', '.join(item['files'])}). It is queued for an explain-back next session."
        )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
