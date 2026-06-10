"""Hook script tests: run capture.py and stop_nudge.py as real subprocesses with realistic hook
JSON on stdin, the way Claude Code invokes them. Verifies accumulation, the threshold, pending
hand-off, and the fail-silent guarantee."""

import json
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def _run(script, payload, env_extra):
    import os

    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, str(_HOOKS / script)],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30,
    )


def test_capture_accumulates_and_stop_writes_pending(tmp_path):
    env = {"FEYNMAN_SCRATCH_DIR": str(tmp_path), "FEYNMAN_HOME": str(tmp_path),
           "FEYNMAN_NUDGE_LINES": "5"}
    sid = "sess1"

    r = _run("capture.py", {"session_id": sid, "tool_name": "Write", "cwd": "/proj",
                            "tool_input": {"file_path": "/proj/api.py", "content": "a\nb\nc\nd"}}, env)
    assert r.returncode == 0
    r = _run("capture.py", {"session_id": sid, "tool_name": "Edit",
                            "tool_input": {"file_path": "/proj/api.py", "new_string": "x\ny\nz"}}, env)
    assert r.returncode == 0
    tally = json.loads((tmp_path / f"feynman_capture_{sid}.json").read_text())
    assert tally["lines"] == 7 and tally["files"]["api.py"] == 7

    r = _run("stop_nudge.py", {"session_id": sid}, env)
    assert r.returncode == 0
    assert "explain-back" in r.stdout                       # the transcript one-liner
    items = json.loads((tmp_path / "feynman_pending.json").read_text())["items"]
    assert items[0]["lines"] == 7 and items[0]["files"] == ["api.py"]
    assert not (tmp_path / f"feynman_capture_{sid}.json").exists()  # tally cleared, no repeat

    r = _run("stop_nudge.py", {"session_id": sid}, env)     # second stop: nothing to say
    assert r.returncode == 0 and r.stdout == ""


def test_below_threshold_stays_silent(tmp_path):
    env = {"FEYNMAN_SCRATCH_DIR": str(tmp_path), "FEYNMAN_HOME": str(tmp_path),
           "FEYNMAN_NUDGE_LINES": "100"}
    _run("capture.py", {"session_id": "s2", "tool_name": "Write",
                        "tool_input": {"file_path": "/p/f.py", "content": "one\ntwo"}}, env)
    r = _run("stop_nudge.py", {"session_id": "s2"}, env)
    assert r.returncode == 0 and r.stdout == ""
    assert not (tmp_path / "feynman_pending.json").exists()


def test_hooks_fail_silent_on_garbage_input(tmp_path):
    env = {"FEYNMAN_SCRATCH_DIR": str(tmp_path), "FEYNMAN_HOME": str(tmp_path)}
    for script in ("capture.py", "stop_nudge.py"):
        r = subprocess.run([sys.executable, str(_HOOKS / script)],
                           input="not json{", capture_output=True, text=True,
                           env={**__import__('os').environ, **env}, timeout=30)
        assert r.returncode == 0  # a hook must NEVER break the host session
