"""Model configuration: which LLM does the judging, and at what cost point.

Users pick their accuracy/cost tradeoff without touching code:
- FEYNMAN_JUDGE_MODEL: the judge + transfer engine (default Opus: highest accuracy).
  Set claude-sonnet-4-6 for ~3x cheaper daily use.
- FEYNMAN_FAST_MODEL: the trivial subtasks (query expansion, relations, miss-tagging).

The host LLM is already provider-free (MCP runs in Claude, ChatGPT, Gemini). The judge currently
requires an Anthropic key; the Judge/TransferEngine interfaces exist so OpenAI/Gemini judges can
be added without touching the rest of the system (see CONTRIBUTING).
"""

from __future__ import annotations

import os

_DEFAULT_JUDGE = "claude-opus-4-8"
_DEFAULT_FAST = "claude-haiku-4-5"


def judge_model() -> str:
    return os.environ.get("FEYNMAN_JUDGE_MODEL", _DEFAULT_JUDGE)


def fast_model() -> str:
    return os.environ.get("FEYNMAN_FAST_MODEL", _DEFAULT_FAST)
