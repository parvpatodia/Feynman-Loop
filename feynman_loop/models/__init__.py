"""Data model for Feynman-Loop, the memory-of-understanding layer (Decisions 9-11).

Three buckets:
- concept     : the atom; stores a LOCATOR to its source of truth, not the truth text.
- user_state  : per (user, concept) tracking; gap detection + hybrid due policy.
- relevance   : goal + many-to-many link; enforces relevance-filtered-at-intake.
"""

from feynman_loop.models.concept import Concept, SourceRef, SourceTier
from feynman_loop.models.gap_report import Citation, Gap, GapReport
from feynman_loop.models.relevance import Goal, GoalStatus, GoalType, RelevanceLink
from feynman_loop.models.transfer import RubricPoint, TransferProbe, TransferResult
from feynman_loop.models.user_state import UserState

__all__ = [
    "Concept",
    "SourceRef",
    "SourceTier",
    "Goal",
    "GoalStatus",
    "GoalType",
    "RelevanceLink",
    "UserState",
    "Citation",
    "Gap",
    "GapReport",
    "RubricPoint",
    "TransferProbe",
    "TransferResult",
]
