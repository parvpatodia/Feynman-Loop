# Feynman-Loop

An AI tool for thought that makes you understand concepts deeply instead of offloading them. You explain a concept, it finds the gap against a trusted source, and resurfaces what you don't yet truly know.

## How it works

Explain-it-back, at two speeds.

**Rapid (default, 2-3 minutes):** say *"quiz me on backprop"*. You get one sharp question per
rubric point, answer each in a line or two from memory, and see an instant verdict and a running
score. Same honest measurement, a fraction of the friction.

**Full:** you explain the whole concept in your own words and it is judged in one pass.

Either way: the rubric is grounded in your own source material, gaps come back as questions
(never answers), a transfer challenge tests whether you can APPLY the concept, and the ledger
records the score and schedules when it comes back. One streak number tracks consistency;
there are no points and no leaderboards.

The project is governed by three docs, read in order: `PRINCIPLES.md` (the constitution), `CONTEXT.md` (current state and decision log), `LEARNINGS.md` (guardrails and findings).

## Setup

**One-click (Claude Desktop, no terminal):** download `feynman-loop.mcpb` from the GitHub
Releases page and double-click it (or Claude Desktop > Settings > Extensions > Install
Extension). The API key field is optional; leave it empty for zero-key mode. Needs Python
3.10+ on your machine. To build the bundle yourself: `./scripts/build_mcpb.sh`.

**Terminal (Claude Code, other MCP hosts, hooks):** use an isolated virtualenv. Do not install
into a global or conda environment, the optional vector stack (numpy, torch) is sensitive to
ABI drift.

```
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e .                    # core: MCP server, pasted-source grounding
.venv/bin/python -m pip install -e ".[embeddings]"      # adds long-document grounding + web UI
.venv/bin/feynman-loop init        # configures the MCP server + Claude Code hooks in one step
```

`init` also prints the config snippet for any other MCP host (ChatGPT Desktop, Gemini, Cursor).
Normal pasted sources are grounded directly (the rubric sees the whole text, instantly); the
`embeddings` extra is only needed for long documents and the web/CLI surfaces.

## No API key? It still works

You do not need an API key to use Feynman-Loop inside an MCP host. With no
`ANTHROPIC_API_KEY` set, the server runs in **zero-key mode**: the chat model you already pay
for (Claude, ChatGPT, Gemini, Cursor) does the language work under a strict protocol, and the
server does the integrity work in code:

- every credited verdict must carry a verbatim quote from YOUR explanation, and the server
  verifies the quote actually appears there; credit it cannot find is downgraded
- rubric quotes are verified against your source passages the same way
- every score is computed in code from the verified statuses, never taken from the model
- minimum rubric sizes per depth are enforced, so a lazy one-point rubric is rejected

With a key, an independent judge model does the rubric building and scoring instead, which is
the strongest setup (your own chat model cannot be talked into leniency at all). The ledger
records which judge scored every event, so your history stays honest about its own strength.
The terminal and web surfaces call the API directly, so those two do need a key.

Storage: one local SQLite ledger plus a markdown knowledge-graph vault, at `$FEYNMAN_HOME`
(default `~/.feynman-loop`). Your data never leaves your machine except for judging calls
(and in zero-key mode, not even that: nothing leaves except what your host chat already sees).
Cost control: set `FEYNMAN_JUDGE_MODEL` (default `claude-opus-4-8`; `claude-sonnet-4-6` is ~3x
cheaper) and `FEYNMAN_FAST_MODEL` (default `claude-haiku-4-5`).

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

You type your explanation, then an empty line to submit. It returns the grounded gaps and, if your explanation is solid, a transfer challenge. Sources can be `.txt` or `.pdf` (text-based PDFs; scanned/image-only PDFs are not OCR'd).

## Run the web UI

```
export ANTHROPIC_API_KEY=...
.venv/bin/python -m uvicorn feynman_loop.web.app:app --port 8000
```

Open http://localhost:8000, paste your source **or upload a .pdf/.txt file**, name the concept, and explain it. The grounded gaps and the transfer challenge render in the browser.
