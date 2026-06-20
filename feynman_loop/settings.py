"""User-selected settings: how proactive the loop is (mode) and WHERE it applies (scope).

The product is opt-in by design (the trust criterion: an interruption is only welcome when the
user invited it). Both knobs are the USER's explicit choice, never inferred or changed by the
system, and both live in $FEYNMAN_HOME/feynman_settings.json.

MODE - how proactive:
- "nudge"  (default): offer an explain-back at a natural boundary (SessionStart); never forced.
- "commit" (self-armed teeth): also gate at session end. If you shipped unexplained AI-written
           code, the Stop hook asks you to explain it before wrapping up. You armed it, you can
           still decline, and it never traps you (the gate fires once, then lets go).
- "off"   : silence every proactive surface (SessionStart context + the daily notification).

SCOPE - where it applies:
- the proactive hooks (SessionStart nudge, line capture, the Stop gate) fire in EVERY project by
  default. A non-empty scope is an allowlist of project directories: outside them the hooks stay
  completely silent and record nothing. The MCP tools themselves stay callable in any host where
  the connector is configured; scope only governs the always-on PUSH, which is the intrusive part.

A stdlib-only hook (capture.py / stop_nudge.py) cannot import this package, so it duplicates the
constants and the in-scope check; keep them in sync (a test pins the constants, and the prior
pending-path divergence is why).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

MODES = ("off", "nudge", "commit")
DEFAULT_MODE = "nudge"
SETTINGS_FILE = "feynman_settings.json"


def _load(root: Path) -> dict:
    """Read the settings dict, degrading any corruption/missing file to {} so callers get the
    safe defaults and a bad file can never crash a hook or produce a surprise behavior."""
    try:
        data = json.loads((root / SETTINGS_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):  # ValueError covers json.JSONDecodeError
        return {}


def _save(root: Path, data: dict) -> None:
    """Persist atomically: write a temp file in the same dir, then os.replace (atomic on POSIX),
    so a crash mid-write can never leave a half-written settings file that reads as garbage."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / SETTINGS_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


# --- mode ---

def get_mode(root: Path) -> str:
    """The user's mode; any unknown/corrupt value degrades to the safe default (never a gate)."""
    mode = _load(root).get("mode")
    return mode if mode in MODES else DEFAULT_MODE


def set_mode(root: Path, mode: str) -> None:
    """Persist the chosen mode. Raises ValueError on an unknown mode so the CLI rejects it."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose one of {', '.join(MODES)}")
    data = _load(root)
    data["mode"] = mode
    _save(root, data)


# --- scope (which projects the proactive hooks fire in) ---

def _norm(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def get_scope(root: Path) -> list[str]:
    """The allowlist of project directories. Empty == every project (the default)."""
    raw = _load(root).get("scope")
    return [p for p in raw if isinstance(p, str)] if isinstance(raw, list) else []


def add_scope_path(root: Path, path: str) -> str:
    """Add a project directory to the allowlist (switches proactivity from everywhere to only the
    listed projects). Returns the normalized absolute path that was stored."""
    p = _norm(path)
    data = _load(root)
    scope = [x for x in (data.get("scope") or []) if isinstance(x, str)]
    if p not in scope:
        scope.append(p)
    data["scope"] = scope
    _save(root, data)
    return p


def remove_scope_path(root: Path, path: str) -> None:
    p = _norm(path)
    data = _load(root)
    data["scope"] = [x for x in (data.get("scope") or []) if isinstance(x, str) and x != p]
    _save(root, data)


def set_scope_all(root: Path) -> None:
    """Reset to firing in every project (clears the allowlist)."""
    data = _load(root)
    data["scope"] = []
    _save(root, data)


def path_in_scope(cwd: str | None, allowed: list[str]) -> bool:
    """Is this session's working directory within the allowlist? Empty allowlist == everywhere.
    An unknown cwd fails OPEN (proactivity shows) so a parse hiccup never silently mutes the loop;
    scoping is a convenience, not a security boundary."""
    if not allowed:
        return True
    if not cwd:
        return True
    c = _norm(cwd)
    return any(c == pre or c.startswith(pre + os.sep) for pre in allowed)


# --- project identity (which project a concept belongs to / a session is in) ---
#
# Scope (above) is a binary gate: does the loop fire here at all. Project is finer: WHICH concepts
# surface. The two compose — scope decides if there is any nudge, project decides what is in it.


def _git_root(cwd: str) -> str | None:
    """The git toplevel of cwd, or None if cwd is not in a repo (or git is unavailable). Args are
    passed as a list (never shell=True), so a hostile cwd can only make git fail, never inject."""
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    top = out.stdout.strip()
    return top or None


def project_for(cwd: str | None) -> str | None:
    """The canonical project id for a working directory: the git repo root if cwd is inside one,
    else the normalized cwd. None when cwd is unknown — callers then fail OPEN (no project filter).

    WHY git root: one repo == one project, so concepts learned anywhere inside a repo share a
    bucket and a sub-directory never forks a repo's history into a separate project. This is the
    single derivation used by BOTH due (to filter) and the MCP server (to tag), so a concept's
    stored tag and the filter key match by construction. Best-effort: no git / not a repo -> cwd."""
    if not cwd:
        return None
    return _norm(_git_root(cwd) or cwd)


def concept_in_project(concept_project: str | None, current_project: str | None) -> bool:
    """Should a concept tagged `concept_project` surface in a session whose project is
    `current_project`? Global (None) concepts surface anywhere; an unknown session project
    (cwd missing) fails OPEN and shows everything; otherwise the tags must match. The asymmetry is
    deliberate: when unsure we OVER-surface (a concept is never silently hidden), never under."""
    if concept_project is None:   # global / uncategorized: belongs to no one project
        return True
    if current_project is None:   # unknown session project: never silently hide a due concept
        return True
    return concept_project == current_project
