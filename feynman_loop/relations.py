"""Concept relations: the edges of the knowledge graph.

One cheap call at intake (Haiku, no thinking) names the concepts that neighbour a new concept,
prerequisites and siblings. Tracked neighbours become graph edges; untracked ones render as the
learner's frontier, the visible map of what they have NOT yet earned.
"""

from __future__ import annotations

from anthropic import Anthropic
from pydantic import BaseModel

from feynman_loop.providers import fast_model

_SYSTEM = """Given a concept name, list 3 to 6 closely related concepts a learner should also
understand: direct prerequisites and immediate siblings. Short canonical names only (e.g.
"Chain Rule", not a sentence). Do not include the concept itself."""


class _Related(BaseModel):
    labels: list[str]


class ClaudeRelatedConcepts:
    def __init__(self, *, client: Anthropic | None = None, model: str | None = None) -> None:
        self._client = client or Anthropic()
        self._model = model or fast_model()

    def related_to(self, concept_label: str) -> list[str]:
        draft: _Related = self._client.messages.parse(
            model=self._model,
            max_tokens=256,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Concept: {concept_label}"}],
            output_format=_Related,
        ).parsed_output
        wanted = concept_label.strip().casefold()
        seen: list[str] = []
        for label in draft.labels:
            clean = label.strip()
            if clean and clean.casefold() != wanted and clean not in seen:
                seen.append(clean)
        return seen[:6]
