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

## Good first issues

- **OpenAI judge**: implement `Judge` + `TransferEngine` backed by the OpenAI SDK so ChatGPT
  users can bring their own key (see `feynman_loop/judge/base.py`; mirror `claude_judge.py`,
  test with a fake client like `tests/test_claude_judge.py`).
- **Gemini judge**: same shape, Google SDK.
- **Journey cards**: render a concept's journey (first words vs latest, score arc, memory
  strength) as a shareable image or terminal card.
- **Rehearsal vs full history**: `loop.is_near_verbatim` compares only the previous attempt;
  compare against all stored attempts for the concept.
- **Web multi-user**: per-user identity + auth so the web app can be deployed for more than one
  person (today it is deliberately single-user, localhost only).
