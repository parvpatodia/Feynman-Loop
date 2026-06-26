"""`feynman due`: read the understanding ledger and report what is due.

Two output modes:
- default: a human-readable summary for the terminal.
- --context: a compact instruction block for a Claude Code SessionStart hook. The host adds the
  hook's stdout to session context, which is how the loop becomes PROACTIVE: the session opens
  already knowing what you owe yourself an explanation for. With --quiet it prints nothing when
  there is nothing actionable, so quiet sessions stay quiet.

The trust criterion governs the wording: the host is told to OFFER a check at a natural moment,
never to force one, and never to answer probes itself. A declined concept simply stays due.

Run: python -m feynman_loop.due [--context] [--quiet]
Ledger location: $FEYNMAN_HOME, defaulting to the repo root.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from feynman_loop import paths, settings
from feynman_loop.db import stores_for
from feynman_loop.learner import derive_profile


def collect(root: Path | None = None, now: datetime | None = None,
            cwd: str | None = None) -> dict:
    """Aggregate the ledger: concepts due now, pending shipped-work nudges, learner profile.
    cwd is the session's working directory (from the hook payload); it decides project scope."""
    root = root or paths.home()
    now = now or datetime.now(timezone.utc)
    stores = stores_for(root)
    uid = stores.identity.user_id()
    store = stores.states

    # The project this session is in (from the git root of cwd). None when cwd is unknown, which
    # makes the project filter fail OPEN — an explicit `feynman-loop due` (no cwd) still shows all.
    current_project = settings.project_for(cwd)

    due, tracked = [], 0
    for c in stores.concepts.all():
        st = store.get(user_id=uid, concept_id=c.id)
        if st is None:
            continue
        tracked += 1
        # project scope: surface this project's concepts plus the global bucket; a concept tagged
        # to a DIFFERENT project does not surface here (tracked still counts it — it is not gone).
        if not settings.concept_in_project(c.project, current_project):
            continue
        if st.next_due_at and st.next_due_at <= now:
            due.append({
                "concept": c.label,
                "understanding": round(st.understanding_level, 2),
                "transfer": round(st.transfer_level, 2) if st.transfer_level is not None else None,
                "due_since": st.next_due_at.strftime("%Y-%m-%d"),
                # WHY: the probe from the last review makes the nudge CONCRETE. "X is due" asks
                # for discipline; an actual 30-second question only asks for an answer. Stored at
                # review time, so surfacing it needs no model call and works everywhere.
                "probe": st.identified_gaps[0] if st.identified_gaps else "",
            })
    due.sort(key=lambda d: d["understanding"])  # weakest first: the rep that matters most

    # pending = AI-written work shipped without an explain-back (written by the Stop hook).
    # Reading consumes it: each item is surfaced once; ignoring it is allowed and costs nothing.
    # Project scope: each item carries the cwd it was shipped in, so surface only THIS project's
    # items (plus global/unknown) and KEEP the rest for their own session. WHY rewrite instead of
    # delete: consume-once must not discard a nudge that belongs to a different project — that would
    # silently drop a real "you shipped code you can't explain" signal the moment you happened to
    # open an unrelated project first.
    pending_path = root / "feynman_pending.json"
    pending: list[dict] = []
    if pending_path.exists():
        try:
            all_items = json.loads(pending_path.read_text()).get("items", [])
        except (json.JSONDecodeError, OSError):
            all_items = []
        others: list[dict] = []
        for item in all_items:
            item_project = settings.project_for(item.get("cwd") or None)
            (pending if settings.concept_in_project(item_project, current_project) else others).append(item)
        if others:
            pending_path.write_text(json.dumps({"items": others}, indent=2))
        else:
            pending_path.unlink(missing_ok=True)

    profile = derive_profile(stores.events.events())
    return {"due": due, "pending": pending, "tracked": tracked, "profile": profile,
            "mode": settings.get_mode(root),
            "in_scope": settings.path_in_scope(cwd, settings.get_scope(root)),
            "current_project": current_project, "cwd": cwd}


def projects(root: Path | None = None) -> list[dict]:
    """Group every tracked concept by its project bucket — the read-only AUDIT view for
    project-scoped recall, so the user can see exactly what is filed where and what is still
    global (which is what would surface everywhere). Read-only: it never mutates the ledger.
    Returns buckets ordered named-projects-first then global, each:
    {"project": <id|None>, "concepts": [{"label", "understanding"}, ...]}."""
    root = root or paths.home()
    stores = stores_for(root)
    uid = stores.identity.user_id()
    grouped: dict[str | None, list[dict]] = {}
    for c in stores.concepts.all():
        st = stores.states.get(user_id=uid, concept_id=c.id)
        grouped.setdefault(c.project, []).append({
            "label": c.label,
            "understanding": round(st.understanding_level, 2) if st else None,
        })
    for items in grouped.values():
        items.sort(key=lambda d: d["label"].casefold())
    # named projects first (sorted), the global bucket last: it is the catch-all, not a project
    named = sorted((p for p in grouped if p is not None), key=str.casefold)
    order = named + ([None] if None in grouped else [])
    return [{"project": p, "concepts": grouped[p]} for p in order]


def render_projects(buckets: list[dict]) -> str:
    """Human-readable rendering of projects(); public because app_cli is the only caller."""
    if not buckets:
        return "No concepts tracked yet."
    out: list[str] = []
    for b in buckets:
        title = (b["project"] if b["project"] is not None
                 else "global / uncategorized (surfaces in every project)")
        out.append(f"{title}  ({len(b['concepts'])})")
        for c in b["concepts"]:
            u = f" — understanding {c['understanding']:.0%}" if c["understanding"] is not None else ""
            out.append(f"  - {c['label']}{u}")
    return "\n".join(out)


def _context_block(data: dict) -> str:
    """The SessionStart context. Only emitted when something is actionable."""
    if data.get("mode") == "off" or not data.get("in_scope", True):
        return ""  # silenced (off) or this project is out of the user's chosen scope
    lines: list[str] = []
    if data["due"]:
        items = ", ".join(
            f"{d['concept']} (last {d['understanding']:.0%})" for d in data["due"][:5]
        )
        lines.append(f"{len(data['due'])} concept(s) due for an explain-back: {items}.")
        top = data["due"][0]
        if top.get("probe"):
            lines.append(
                f'Micro-rep for "{top["concept"]}": ask the user this 30-second question near '
                f'the start of the session, to be answered from memory in their own words: '
                f'"{top["probe"]}"'
            )
    for p in data["pending"][:3]:
        files = ", ".join(p.get("files", [])[:3])
        lines.append(
            f"Recently shipped ~{p.get('lines', '?')} AI-written lines in {p.get('cwd', 'a project')}"
            f" ({files}) without an explain-back."
        )
    if data["pending"]:
        # WHY: "you shipped code you can't explain" is a dead-end guilt notice unless it feeds the
        # actual loop. Bridge it: the host READS the shipped file and passes it as source_text to
        # start_check, so the check is grounded in the very code the learner shipped, then THEY
        # explain it. The server never stores code (privacy property, Decision 20); the host, which
        # already has the file open, reads it live. Still an OFFER at a natural boundary, never a
        # gate (the trust criterion + no forced interruption in v1).
        target = next((f for p in data["pending"] for f in p.get("files", []) if f),
                      "the file you shipped")
        lines.append(
            f"To turn that shipped code into understanding (not a guilt notice): at a natural "
            f"moment, OFFER to read {target} yourself and pass it as source_text to start_check, "
            f"so the check is grounded in the code they shipped; then have the learner explain, in "
            f"their own words, what it does and why. If they pass, it just stays for next time."
        )
    # WHY: spaced recall is project-scoped, but the MCP server that mints concepts never sees the
    # session cwd (it is launched from an arbitrary directory). So the one surface that DOES have
    # the cwd — this hook — tells the host to pass it to start_check, which lets the server file the
    # new concept under THIS project. Omitting it is safe: the concept just stays global (surfaces
    # everywhere), never mis-filed. The literal cwd is given so the host copies it verbatim.
    if lines and data.get("cwd"):
        lines.append(
            f'This session is in project "{data["cwd"]}". When you OFFER an explain-back and call '
            f'start_check, pass cwd="{data["cwd"]}" so the new concept is filed under THIS project '
            f"and resurfaces here, not in unrelated sessions."
        )
    insight = data["profile"].get("insight", "")
    if lines and insight and insight != "No reviews yet.":
        lines.append(f"Learner pattern: {insight}")
    if not lines:
        return ""
    guidance = (
        "If a natural moment arises this session, OFFER the user a short explain-back using the "
        "feynman-loop MCP tools (start_check, then judge_explanation). Do not force it; if the "
        "user declines, drop the subject — the concept simply stays due. Relay probes for the "
        "user to answer in their own words; never answer them yourself."
    )
    return "<feynman-loop>\n" + "\n".join(lines) + "\n" + guidance + "\n</feynman-loop>"


def _human(data: dict) -> str:
    out: list[str] = [f"Tracked concepts: {data['tracked']}"]
    if data["due"]:
        out.append("Due now:")
        for d in data["due"]:
            t = f", transfer {d['transfer']:.0%}" if d["transfer"] is not None else ""
            out.append(f"  - {d['concept']} (understanding {d['understanding']:.0%}{t}, due since {d['due_since']})")
    else:
        out.append("Nothing due.")
    insight = data["profile"].get("insight", "")
    if insight:
        out.append(f"Learner: {insight}")
    return "\n".join(out)


def _notification_text(data: dict) -> str:
    """The OS-notification line: one concrete question, not a guilt counter. A live streak is
    named because protecting it is the one gamified motivation we allow (consistency, not rank)."""
    if data.get("mode") == "off" or not data["due"]:
        return ""
    top = data["due"][0]
    streak = data["profile"].get("streak_days", 0)
    prefix = f"Day {streak + 1}: " if streak >= 2 else ""
    if top.get("probe"):
        return f"{prefix}{top['concept']}: {top['probe']}"[:180]
    more = f" (+{len(data['due']) - 1} more)" if len(data["due"]) > 1 else ""
    return f"{prefix}30-second rep due: {top['concept']}{more}"


def _applescript_string(text: str) -> str:
    """A safe AppleScript string literal. WHY not plain json.dumps: JSON escapes control and
    (by default) non-ASCII characters as \\uXXXX, which AppleScript's parser does not understand,
    so a concept named "Poincaré" or a probe with a stray control char would silently kill the
    notification. AppleScript only needs backslash and double-quote escaped; control chars are
    dropped (they have no place in a notification line)."""
    cleaned = "".join(ch for ch in text if ch >= " " or ch == "\t")
    return '"' + cleaned.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _post_notification(text: str) -> None:
    """macOS notification via osascript; prints as fallback elsewhere. This is the only true
    PUSH channel we have: hosts cannot be pushed into (MCP is pull-only), the OS can."""
    import platform
    import subprocess

    if platform.system() == "Darwin":
        script = f'display notification {_applescript_string(text)} with title "Feynman-Loop"'
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="feynman-due", add_help=True)
    parser.add_argument("--context", action="store_true", help="emit a SessionStart context block")
    parser.add_argument("--quiet", action="store_true", help="print nothing when nothing is actionable")
    parser.add_argument("--notify", action="store_true",
                        help="post an OS notification with the top due question (for launchd/cron)")
    args = parser.parse_args(argv)

    # WHY: the SessionStart hook pipes its JSON payload (including cwd) on stdin. Read it ONLY in
    # --context mode and only when stdin is not a terminal, so a manual `feynman-loop due` never
    # blocks waiting for input. cwd drives project scope; any hiccup leaves it None (fails open).
    cwd = None
    if args.context and not sys.stdin.isatty():
        try:
            payload = json.load(sys.stdin)
            cwd = payload.get("cwd") if isinstance(payload, dict) else None
        except (ValueError, OSError):
            cwd = None

    try:
        data = collect(cwd=cwd)
    except Exception:
        # WHY: a hook must never break the host session. No ledger -> say nothing.
        return 0

    if args.notify:
        text = _notification_text(data)
        if text:
            _post_notification(text)
        return 0
    if args.context:
        block = _context_block(data)
        if block:
            print(block)
        return 0
    if args.quiet and not data["due"] and not data["pending"]:
        return 0
    print(_human(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
