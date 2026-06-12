"""SQLite-backed ledger stores.

Why SQLite replaces the JSON files: three processes write the ledger concurrently on a normal
machine (Claude Desktop's MCP server, a terminal Claude Code MCP server, the web app). The JSON
stores did read-file -> modify -> write-whole-file, so simultaneous writers lost each other's
updates. SQLite with WAL gives row-level upserts and real multi-process safety, still one local
file, still the user's own data.

Rows hold the pydantic JSON dump, so the schema stays stable while models evolve. The ledger
remains the source of truth; everything else (vault, maps) is a regenerated view.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from feynman_loop.learner import JsonLearnerLog, ReviewEvent
from feynman_loop.models import Concept, UserState
from feynman_loop.storage import JsonConceptStore, JsonIdentity

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_state (
  user_id TEXT NOT NULL, concept_id TEXT NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (user_id, concept_id));
CREATE TABLE IF NOT EXISTS concepts (
  id TEXT PRIMARY KEY, label_norm TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_concepts_label ON concepts(label_norm);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
"""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _init_db(path: Path) -> None:
    with _connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # persistent once set; enables concurrent readers
        conn.executescript(_SCHEMA)
    try:
        # WHY: the ledger holds the user's own explanations (personal learning data); keep it
        # owner-only on shared machines. Best-effort: not all filesystems support chmod.
        path.chmod(0o600)
    except OSError:
        pass


class SqliteUserStateStore:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        _init_db(self._path)

    def get(self, *, user_id: UUID, concept_id: UUID) -> UserState | None:
        with _connect(self._path) as conn:
            row = conn.execute(
                "SELECT data FROM user_state WHERE user_id=? AND concept_id=?",
                (str(user_id), str(concept_id)),
            ).fetchone()
        return UserState.model_validate(json.loads(row[0])) if row else None

    def put(self, state: UserState) -> None:
        with _connect(self._path) as conn:
            conn.execute(
                "INSERT INTO user_state (user_id, concept_id, data) VALUES (?,?,?) "
                "ON CONFLICT(user_id, concept_id) DO UPDATE SET data=excluded.data",
                (str(state.user_id), str(state.concept_id), state.model_dump_json()),
            )

    def all(self) -> list[UserState]:
        with _connect(self._path) as conn:
            rows = conn.execute("SELECT data FROM user_state").fetchall()
        return [UserState.model_validate(json.loads(r[0])) for r in rows]


class SqliteConceptStore:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        _init_db(self._path)

    def put(self, concept: Concept) -> None:
        with _connect(self._path) as conn:
            conn.execute(
                "INSERT INTO concepts (id, label_norm, data) VALUES (?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET label_norm=excluded.label_norm, data=excluded.data",
                (str(concept.id), concept.label.strip().casefold(), concept.model_dump_json()),
            )

    def get(self, concept_id: UUID) -> Concept | None:
        with _connect(self._path) as conn:
            row = conn.execute("SELECT data FROM concepts WHERE id=?", (str(concept_id),)).fetchone()
        return Concept.model_validate(json.loads(row[0])) if row else None

    def find_by_label(self, label: str) -> Concept | None:
        with _connect(self._path) as conn:
            row = conn.execute(
                "SELECT data FROM concepts WHERE label_norm=?", (label.strip().casefold(),)
            ).fetchone()
        return Concept.model_validate(json.loads(row[0])) if row else None

    def all(self) -> list[Concept]:
        with _connect(self._path) as conn:
            rows = conn.execute("SELECT data FROM concepts ORDER BY rowid").fetchall()
        return [Concept.model_validate(json.loads(r[0])) for r in rows]


class SqliteLearnerLog:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        _init_db(self._path)

    def append(self, event: ReviewEvent) -> None:
        with _connect(self._path) as conn:
            conn.execute("INSERT INTO events (data) VALUES (?)", (event.model_dump_json(),))

    def events(self) -> list[ReviewEvent]:
        with _connect(self._path) as conn:
            rows = conn.execute("SELECT data FROM events ORDER BY seq").fetchall()
        return [ReviewEvent.model_validate(json.loads(r[0])) for r in rows]


@dataclass
class LedgerStores:
    identity: JsonIdentity
    states: SqliteUserStateStore
    concepts: SqliteConceptStore
    events: SqliteLearnerLog


def _migrate_json_if_needed(root: Path, db_path: Path) -> None:
    """One-time import of the legacy JSON ledger into SQLite. Idempotent: runs only while the
    DB is empty; imported files are renamed *.imported so old data is kept but never re-read."""
    legacy = {
        "states": root / "feynman_state.json",
        "concepts": root / "feynman_concepts.json",
        "events": root / "feynman_learner.json",
    }
    if not any(p.exists() for p in legacy.values()):
        return
    with _connect(db_path) as conn:
        have = sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
                   for t in ("user_state", "concepts", "events"))
    if have:
        return
    if legacy["concepts"].exists():
        store = SqliteConceptStore(db_path)
        for c in JsonConceptStore(legacy["concepts"]).all():
            store.put(c)
    if legacy["states"].exists():
        store = SqliteUserStateStore(db_path)
        raw = json.loads(legacy["states"].read_text())
        for item in raw.values():
            store.put(UserState.model_validate(item))
    if legacy["events"].exists():
        log = SqliteLearnerLog(db_path)
        for e in JsonLearnerLog(legacy["events"]).events():
            log.append(e)
    for p in legacy.values():
        if p.exists():
            p.rename(p.with_suffix(p.suffix + ".imported"))


def stores_for(root: Path) -> LedgerStores:
    """The single place that decides the storage backend. Every surface (MCP, web, due CLI,
    vault) goes through here, so they always share one ledger."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "feynman.db"
    _init_db(db_path)
    _migrate_json_if_needed(root, db_path)
    return LedgerStores(
        identity=JsonIdentity(root / "feynman_user.json"),
        states=SqliteUserStateStore(db_path),
        concepts=SqliteConceptStore(db_path),
        events=SqliteLearnerLog(db_path),
    )


def export_ledger(root: Path) -> dict:
    """The full ledger as plain JSON: concepts (rubrics, snapshots), per-concept state, every
    review event, and the local identity. WHY this exists: the ledger is the product, and data
    a user cannot take with them is data held hostage. Local-first means one-command export."""
    from datetime import datetime, timezone

    stores = stores_for(root)
    return {
        "feynman_loop_export": 1,  # format version for future importers
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_id": str(stores.identity.user_id()),
        "concepts": [c.model_dump(mode="json") for c in stores.concepts.all()],
        "states": [s.model_dump(mode="json") for s in stores.states.all()],
        "events": [e.model_dump(mode="json") for e in stores.events.events()],
    }
