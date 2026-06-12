"""Model configuration: which LLM does the judging, and at what cost point.

Users pick their accuracy/cost tradeoff without touching code:
- FEYNMAN_JUDGE_MODEL: the judge + transfer engine (default Opus: highest accuracy).
  Set claude-sonnet-4-6 for ~3x cheaper daily use.
- FEYNMAN_FAST_MODEL: the trivial subtasks (query expansion, relations, miss-tagging).

The host LLM is already provider-free (MCP runs in Claude, ChatGPT, Gemini). The key is optional:
with one, an independent judge model scores (strongest); without one, the MCP server runs in
zero-key mode and the host model judges under the verified-evidence protocol (verification.py).
The Judge/TransferEngine interfaces exist so OpenAI/Gemini judges can be added without touching
the rest of the system (see CONTRIBUTING).
"""

from __future__ import annotations

import os

_DEFAULT_JUDGE = "claude-opus-4-8"
_DEFAULT_FAST = "claude-haiku-4-5"


def has_api_key() -> bool:
    """Whether an independent judge is available. Without a key, the MCP server switches to
    zero-key mode: the HOST model does the language work under a strict protocol and the server
    verifies evidence and computes every score in code (see verification.py).

    WHY the extra checks: MCP hosts launch the server with templated env (the .mcpb manifest
    uses ${user_config.anthropic_api_key}). An empty field can arrive as "", whitespace, or the
    UNSUBSTITUTED literal placeholder; treating any of those as a key would select independent
    mode and 401 on every judge call, breaking exactly the keyless users the bundle targets."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return bool(key) and not key.startswith("${")


def judge_model() -> str:
    return os.environ.get("FEYNMAN_JUDGE_MODEL", _DEFAULT_JUDGE)


def fast_model() -> str:
    return os.environ.get("FEYNMAN_FAST_MODEL", _DEFAULT_FAST)
