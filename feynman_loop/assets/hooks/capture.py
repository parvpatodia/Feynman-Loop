#!/usr/bin/env python3
"""Claude Code PostToolUse hook: count the lines the AI wrote this session.

Receives the hook JSON on stdin for Edit/Write/MultiEdit tool calls and accumulates a per-session
tally (lines + files) in a scratch file. The Stop hook (stop_nudge.py) turns a big enough tally
into a pending explain-back nudge. Counting is deliberately simple: lines of NEW content the AI
produced. It is a signal for "you shipped code you didn't write", not a precise diff stat.

Stdlib only, and every failure exits 0: a hook must never break the host session.
"""

import json
import os
import sys
import tempfile
from pathlib import Path


def added_lines(tool_name: str, tool_input: dict) -> int:
    if tool_name == "Write":
        content = tool_input.get("content") or ""
        return len(content.splitlines())
    if tool_name == "Edit":
        return len((tool_input.get("new_string") or "").splitlines())
    if tool_name == "MultiEdit":
        return sum(len((e.get("new_string") or "").splitlines())
                   for e in tool_input.get("edits", []))
    return 0


def scratch_path(session_id: str) -> Path:
    base = os.environ.get("FEYNMAN_SCRATCH_DIR") or tempfile.gettempdir()
    return Path(base) / f"feynman_capture_{session_id}.json"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id") or "unknown"
        n = added_lines(payload.get("tool_name", ""), payload.get("tool_input") or {})
        if n <= 0:
            return 0
        path = scratch_path(session_id)
        data = {"lines": 0, "files": {}, "cwd": payload.get("cwd", "")}
        if path.exists():
            data = json.loads(path.read_text())
        data["lines"] = data.get("lines", 0) + n
        file_path = (payload.get("tool_input") or {}).get("file_path", "")
        if file_path:
            name = Path(file_path).name
            data.setdefault("files", {})[name] = data["files"].get(name, 0) + n
        path.write_text(json.dumps(data))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
