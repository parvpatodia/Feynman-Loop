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

Point it at a plain-text source, name a concept, and give a retrieval query:

```
.venv/bin/python -m feynman_loop.cli path/to/source.txt "Backpropagation" "backprop gradients chain rule optimizer"
```

You type your explanation, then an empty line to submit. It returns the grounded gaps and the next-due date. Plain-text sources only for now (no PDF parsing yet).
