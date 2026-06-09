"""JSON-backed persistence for UserState (Decision 14: demo persistence = JSON).

Keyed by (user_id, concept_id). Tiny on purpose. It sits behind these two methods, so swapping
to SQLite/Postgres later means one new class with the same `get`/`put`, no caller changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from feynman_loop.models import UserState


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
