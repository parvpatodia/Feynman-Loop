"""JSON-backed persistence (Decision 14: demo persistence = JSON).

Three small stores, each behind get/put-style methods so swapping to SQLite/Postgres later means
one new class per store, no caller changes:
- JsonUserStateStore: per (user, concept) review state.
- JsonIdentity: a stable local user id. The memory of a user's understanding is only a memory if
  it survives restarts; a per-process uuid orphans the entire history.
- JsonConceptStore: concepts (label, source_ref, rubric), so a re-explained concept attaches to
  its existing history instead of forking a duplicate, and progress can be read after a restart.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from feynman_loop.models import Concept, UserState


class JsonUserStateStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if self._path.exists():
            return json.loads(self._path.read_text())
        return {}

    @staticmethod
    def _key(user_id: UUID, concept_id: UUID) -> str:
        return f"{user_id}:{concept_id}"

    def get(self, *, user_id: UUID, concept_id: UUID) -> UserState | None:
        raw = self._data.get(self._key(user_id, concept_id))
        return UserState.model_validate(raw) if raw else None

    def put(self, state: UserState) -> None:
        self._data[self._key(state.user_id, state.concept_id)] = state.model_dump(mode="json")
        self._path.write_text(json.dumps(self._data, indent=2, default=str))


class JsonIdentity:
    """A stable local user id, created once and reused forever."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def user_id(self) -> UUID:
        if self._path.exists():
            return UUID(json.loads(self._path.read_text())["user_id"])
        uid = uuid4()
        self._path.write_text(json.dumps({"user_id": str(uid)}))
        return uid


class JsonConceptStore:
    """Persist concepts so the understanding ledger survives restarts."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._data: dict[str, dict] = (
            json.loads(self._path.read_text()) if self._path.exists() else {}
        )

    def put(self, concept: Concept) -> None:
        self._data[str(concept.id)] = concept.model_dump(mode="json")
        self._path.write_text(json.dumps(self._data, indent=2, default=str))

    def get(self, concept_id: UUID) -> Concept | None:
        raw = self._data.get(str(concept_id))
        return Concept.model_validate(raw) if raw else None

    def find_by_label(self, label: str) -> Concept | None:
        # WHY: "Backprop" next week must attach to the same concept as "backprop" last week,
        # otherwise the memory forks and resurfacing breaks.
        wanted = label.strip().casefold()
        for raw in self._data.values():
            if raw["label"].strip().casefold() == wanted:
                return Concept.model_validate(raw)
        return None

    def all(self) -> list[Concept]:
        return [Concept.model_validate(raw) for raw in self._data.values()]
