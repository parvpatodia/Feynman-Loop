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

from feynman_loop import paths
from feynman_loop.db import stores_for
from feynman_loop.learner import derive_profile


def collect(root: Path | None = None, now: datetime | None = None) -> dict:
    """Aggregate the ledger: concepts due now, pending shipped-work nudges, learner profile."""
    root = root or paths.home()
    now = now or datetime.now(timezone.utc)
    stores = stores_for(root)
    uid = stores.identity.user_id()
    store = stores.states

    due, tracked = [], 0
    for c in stores.concepts.all():
        st = store.get(user_id=uid, concept_id=c.id)
        if st is None:
            continue
        tracked += 1
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
    pending_path = root / "feynman_pending.json"
    pending: list[dict] = []
    if pending_path.exists():
        try:
            pending = json.loads(pending_path.read_text()).get("items", [])
        except (json.JSONDecodeError, OSError):
            pending = []
        pending_path.unlink(missing_ok=True)

    profile = derive_profile(stores.events.events())
    return {"due": due, "pending": pending, "tracked": tracked, "profile": profile}


def _context_block(data: dict) -> str:
    """The SessionStart context. Only emitted when something is actionable."""
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
    if not data["due"]:
        return ""
    top = data["due"][0]
    streak = data["profile"].get("streak_days", 0)
    prefix = f"Day {streak + 1}: " if streak >= 2 else ""
    if top.get("probe"):
        return f"{prefix}{top['concept']}: {top['probe']}"[:180]
    more = f" (+{len(data['due']) - 1} more)" if len(data["due"]) > 1 else ""
    return f"{prefix}30-second rep due: {top['concept']}{more}"


def _post_notification(text: str) -> None:
    """macOS notification via osascript; prints as fallback elsewhere. This is the only true
    PUSH channel we have: hosts cannot be pushed into (MCP is pull-only), the OS can."""
    import json as _json
    import platform
    import subprocess

    if platform.system() == "Darwin":
        script = f'display notification {_json.dumps(text)} with title "Feynman-Loop"'
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

    try:
        data = collect()
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
