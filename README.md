# Feynman-Loop

An AI tool for thought that makes you understand concepts deeply instead of offloading them. You explain a concept, it finds the gap against a trusted source, and resurfaces what you don't yet truly know.

## How it works

Explain-it-back. You explain a concept in your own words. The system retrieves the relevant passage from your own source material, judges your explanation only against that passage, and shows you the specific gaps, each grounded in a quote from the source. It records what you understood and computes when the concept is due to come back.

The project is governed by three docs, read in order: `PRINCIPLES.md` (the constitution), `CONTEXT.md` (current state and decision log), `LEARNINGS.md` (guardrails and findings).

## Setup

Use an isolated virtualenv. Do not install into a global or conda environment, the dependencies (numpy, sklearn, torch via sentence-transformers) are sensitive to ABI drift.

```
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt
```

## Test

```
.venv/bin/python -m pytest -q
```

## Run the demo (CLI)

Set your Anthropic key in the shell only. Never commit it or paste it anywhere.

```
export ANTHROPIC_API_KEY=...
```

Point it at a plain-text source and name a concept (the retrieval query is derived from the concept automatically):

```
.venv/bin/python -m feynman_loop.cli path/to/source.txt "Backpropagation"
```

You type your explanation, then an empty line to submit. It returns the grounded gaps and, if your explanation is solid, a transfer challenge. Plain-text sources only for now (no PDF parsing yet).

## Run the web UI

```
export ANTHROPIC_API_KEY=...
.venv/bin/python -m uvicorn feynman_loop.web.app:app --port 8000
```

Open http://localhost:8000, paste your source, name the concept, and explain it. The grounded gaps and the transfer challenge render in the browser.
