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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from feynman_loop.learner import JsonLearnerLog, derive_profile
from feynman_loop.storage import JsonConceptStore, JsonIdentity, JsonUserStateStore

_ROOT = Path(__file__).resolve().parent.parent


def _home() -> Path:
    return Path(os.environ.get("FEYNMAN_HOME", _ROOT))


def collect(root: Path | None = None, now: datetime | None = None) -> dict:
    """Aggregate the ledger: concepts due now, pending shipped-work nudges, learner profile."""
    root = root or _home()
    now = now or datetime.now(timezone.utc)
    uid = JsonIdentity(root / "feynman_user.json").user_id()
    store = JsonUserStateStore(root / "feynman_state.json")

    due, tracked = [], 0
    for c in JsonConceptStore(root / "feynman_concepts.json").all():
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
            })

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

    profile = derive_profile(JsonLearnerLog(root / "feynman_learner.json").events())
    return {"due": due, "pending": pending, "tracked": tracked, "profile": profile}


def _context_block(data: dict) -> str:
    """The SessionStart context. Only emitted when something is actionable."""
    lines: list[str] = []
    if data["due"]:
        items = ", ".join(
            f"{d['concept']} (last {d['understanding']:.0%})" for d in data["due"][:5]
        )
        lines.append(f"{len(data['due'])} concept(s) due for an explain-back: {items}.")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="feynman-due", add_help=True)
    parser.add_argument("--context", action="store_true", help="emit a SessionStart context block")
    parser.add_argument("--quiet", action="store_true", help="print nothing when nothing is actionable")
    args = parser.parse_args(argv)

    try:
        data = collect()
    except Exception:
        # WHY: a hook must never break the host session. No ledger -> say nothing.
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
