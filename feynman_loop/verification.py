"""Code-side verification of LLM verdicts: the model proposes, the code disposes.

Any LLM that judges an explanation (the independent API judge, or the HOST model in zero-key
mode) must return, for every non-"missed" rubric point, a VERBATIM evidence quote from the
learner's explanation. This module checks that the quote actually appears there. A verdict whose
evidence cannot be found is downgraded in code.

WHY this exists: it is the integrity layer that makes zero-key mode possible at all. The host
model is the user's own agent and can be talked into leniency, but it cannot fabricate a quote
that this check will not find. The same check catches inflated "met"s from the independent
judge, so requiring evidence raises accuracy in BOTH modes.
"""

from __future__ import annotations

from difflib import SequenceMatcher

STATUS_VALUE = {"met": 1.0, "partial": 0.5, "missed": 0.0}

# Evidence shorter than this proves nothing ("it", "the chain"); treated as missing evidence.
_MIN_EVIDENCE_CHARS = 10

# Fuzzy fallback for quoting drift (a smart quote, an elided word). Tight on purpose: it rescues
# transcription noise, never a paraphrase passed off as a quote.
_FUZZY_FLOOR = 0.9


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def evidence_supported(text: str, evidence: str) -> bool:
    """True if the evidence quote genuinely appears in the text (normalized, near-verbatim).

    Fuzzy rule: at least 90% of the quote's characters must appear IN ORDER in the text
    (sum of matching blocks). That survives a dropped apostrophe or one elided word anywhere
    in the quote, while a paraphrase or invented quote cannot reach 90% in-order overlap."""
    ev, body = _norm(evidence), _norm(text)
    if len(ev) < _MIN_EVIDENCE_CHARS or not body:
        return False
    if ev in body:
        return True
    sm = SequenceMatcher(None, body, ev, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    return matched >= int(len(ev) * _FUZZY_FLOOR)


def verified_status(*, status: str, evidence: str, text: str) -> tuple[str, bool]:
    """Apply the evidence rule to one verdict. Returns (effective_status, evidence_ok).

    - "missed" needs no evidence.
    - "met" without verifiable evidence -> "partial". WHY partial and not missed: half credit
      bounds the damage in both directions (a hallucinating judge can't grant full credit; a
      judge that merely quoted sloppily doesn't zero out a point the learner may have made).
    - "partial" without verifiable evidence -> "missed" (nothing in the text supports any credit).
    Unknown statuses are treated as "missed" so a malformed verdict can never inflate the score.
    """
    if status not in STATUS_VALUE:
        return "missed", False
    if status == "missed":
        return "missed", True
    if evidence_supported(text, evidence):
        return status, True
    return ("partial", False) if status == "met" else ("missed", False)
