"""Export the understanding ledger as a knowledge-graph vault (plain markdown + wikilinks).

Why: people's AI usage produces no durable artifact of what they actually know. Notes apps store
collected text, which is external storage, which is offloading. This vault is different by
construction: a node exists only because the learner explained the concept, and its status is
earned through retrieval over time (the gated intervals). Markdown with [[wikilinks]] means
Obsidian renders it as a graph natively; point FEYNMAN_VAULT inside an existing vault to merge
verified knowledge into the graph the user already lives in.

The ledger stays the source of truth. The vault is a regenerated view, safe to delete.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from feynman_loop.db import stores_for

_STATUS_ORDER = ["due", "fragile", "consolidating", "strong", "untested"]


def safe_name(label: str) -> str:
    return re.sub(r"[^\w\s.\-]", "", label).strip() or "concept"


def status_of(*, interval_days: float | None, due_now: bool, reviewed: bool) -> str:
    """Node status, earned only through retrieval. Thresholds map interval (memory strength)
    to a coarse, honest state; 'untested' means seeded but never explained."""
    if not reviewed:
        return "untested"
    if due_now:
        return "due"
    if interval_days is None or interval_days < 3:
        return "fragile"
    if interval_days < 14:
        return "consolidating"
    return "strong"


def collect_graph(root: Path, now: datetime | None = None) -> list[dict]:
    """One row per tracked concept: status, scores, neighbours, journey, latest words."""
    now = now or datetime.now(timezone.utc)
    stores = stores_for(root)
    uid = stores.identity.user_id()
    states = stores.states
    events = stores.events.events()

    rows: list[dict] = []
    for c in stores.concepts.all():
        st = states.get(user_id=uid, concept_id=c.id)
        interval_days = None
        due_now = False
        if st and st.next_due_at and st.last_reviewed_at:
            interval_days = round((st.next_due_at - st.last_reviewed_at).total_seconds() / 86400, 1)
            due_now = st.next_due_at <= now
        mine = [e for e in events if e.concept_label.strip().casefold() == c.label.strip().casefold()]
        latest_words = next((e.explanation for e in reversed(mine) if e.kind == "explain" and e.explanation), "")
        rows.append({
            "label": c.label,
            "status": status_of(interval_days=interval_days, due_now=due_now, reviewed=st is not None),
            "understanding": round(st.understanding_level, 2) if st else None,
            "transfer": round(st.transfer_level, 2) if st and st.transfer_level is not None else None,
            "memory_strength_days": interval_days,
            "next_due": st.next_due_at.strftime("%Y-%m-%d") if st and st.next_due_at else "",
            "related": list(c.related),
            "latest_words": latest_words,
            "journey": [{"at": e.at.strftime("%Y-%m-%d"), "kind": e.kind, "score": round(e.score, 2)} for e in mine],
        })
    return rows


def sync_vault(root: Path, vault_dir: Path | None = None, now: datetime | None = None) -> Path:
    """Regenerate the markdown vault from the ledger. Idempotent; called after every event."""
    vault = vault_dir or Path(os.environ.get("FEYNMAN_VAULT", str(root / "vault")))
    vault.mkdir(parents=True, exist_ok=True)
    rows = collect_graph(root, now=now)

    for r in rows:
        links = " ".join(f"[[{safe_name(x)}]]" for x in r["related"]) or "_none recorded_"
        words = f"> {r['latest_words']}" if r["latest_words"] else "_no explanation recorded yet_"
        journey = "\n".join(f"- {j['at']} {j['kind']} {j['score']:.0%}" for j in r["journey"]) or "_none yet_"
        understanding = f"{r['understanding']:.0%}" if r["understanding"] is not None else "untested"
        transfer = f"{r['transfer']:.0%}" if r["transfer"] is not None else "untested"
        strength = r["memory_strength_days"] if r["memory_strength_days"] is not None else "n/a"
        body = (
            f"---\nstatus: {r['status']}\nunderstanding: {understanding}\ntransfer: {transfer}\n"
            f"memory_strength_days: {strength}\nnext_due: {r['next_due']}\n---\n\n"
            f"# {r['label']}\n\n"
            f"## In my own words (latest)\n{words}\n\n"
            f"## Related\n{links}\n\n"
            f"## Journey\n{journey}\n"
        )
        (vault / f"{safe_name(r['label'])}.md").write_text(body)

    by_status: dict[str, list[str]] = {}
    for r in rows:
        by_status.setdefault(r["status"], []).append(r["label"])
    index = ["# Feynman Knowledge Map\n", "_Every node here was earned by explaining, not collected._\n"]
    for status in _STATUS_ORDER:
        if status in by_status:
            index.append(f"\n## {status}\n")
            index.extend(f"- [[{safe_name(label)}]]\n" for label in sorted(by_status[status]))
    (vault / "Feynman Knowledge Map.md").write_text("".join(index))
    return vault


def mermaid_map(root: Path, now: datetime | None = None) -> str:
    """The same graph as inline mermaid, so any MCP host (Claude, ChatGPT, Gemini) can render
    the learner's knowledge map directly in the chat window."""
    rows = collect_graph(root, now=now)
    if not rows:
        return ""

    def node_id(label: str) -> str:
        return "n_" + re.sub(r"\W", "_", label.strip().casefold())

    lines = ["graph TD"]
    tracked = {r["label"].strip().casefold(): r for r in rows}
    ghosts: dict[str, str] = {}
    for r in rows:
        pct = f" {r['understanding']:.0%}" if r["understanding"] is not None else ""
        lines.append(f'    {node_id(r["label"])}["{r["label"]}{pct} ({r["status"]})"]')
    for r in rows:
        for rel in r["related"]:
            rid = node_id(rel)
            if rel.strip().casefold() not in tracked and rid not in ghosts:
                ghosts[rid] = rel
                lines.append(f'    {rid}(("{rel}"))')  # frontier: known-unknown, not yet earned
            lines.append(f"    {node_id(r['label'])} --- {rid}")
    lines.append("    classDef due fill:#7a3d2e,color:#fff")
    lines.append("    classDef fragile fill:#7a5c2e,color:#fff")
    lines.append("    classDef consolidating fill:#3d6b4f,color:#fff")
    lines.append("    classDef strong fill:#2e7a52,color:#fff")
    for r in rows:
        if r["status"] in ("due", "fragile", "consolidating", "strong"):
            lines.append(f"    class {node_id(r['label'])} {r['status']}")
    return "\n".join(lines)
