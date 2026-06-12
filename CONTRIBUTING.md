# Contributing

Feynman-Loop is local-first and bring-your-own-key: each user runs it on their machine, their
ledger never leaves their disk except for judging calls. Keep that property in every change.

## Setup

```
python3 -m venv .venv
.venv/bin/pip install -e . && .venv/bin/pip install pytest ruff httpx
.venv/bin/python -m pytest -q     # all tests run offline, no API key needed
```

Rules: tests alongside code, `ruff check --select F,E9,B` clean, no API keys or user data in
commits. The ledger (SQLite + vault) is the product; the LLM is a swappable component behind the
`Judge` / `TransferEngine` interfaces.

CI runs three jobs on every push/PR: the full suite with the embeddings extra (3.10 and 3.12),
a core-only install proving the MCP server works without the extra (the zero-key bundle
configuration; keep it passing), and a non-blocking dependency audit. A change that makes the
core job need the embeddings extra is a regression even if all tests pass.

Judging has two modes (see `mcp_server.py` docstring): an independent API judge when a key is
set, and zero-key mode where the MCP host model judges under the verified-evidence protocol
(`verification.py`). Any new judge implementation must keep the evidence rule: credited verdicts
carry verbatim quotes, verified in code, score computed in code.

## Good first issues

- **OpenAI judge**: implement `Judge` + `TransferEngine` backed by the OpenAI SDK so ChatGPT
  users can bring their own key (see `feynman_loop/judge/base.py`; mirror `claude_judge.py`,
  test with a fake client like `tests/test_claude_judge.py`).
- **Gemini judge**: same shape, Google SDK. Gemini's API has a free tier (no card), which makes
  this the cheapest independent-judge path.
- **MCP sampling judge**: when MCP hosts ship `sampling/createMessage` support, route judge
  calls through the host's own subscription with server-authored prompts: independent-judge
  prompts with zero keys anywhere. Claude Desktop does not support sampling yet
  (anthropics/claude-code#1785 tracks Claude Code).
- **Journey cards**: render a concept's journey (first words vs latest, score arc, memory
  strength) as a shareable image or terminal card.
- **Rehearsal vs full history**: `loop.is_near_verbatim` compares only the previous attempt;
  compare against all stored attempts for the concept.
- **Web multi-user**: per-user identity + auth so the web app can be deployed for more than one
  person (today it is deliberately single-user, localhost only).
