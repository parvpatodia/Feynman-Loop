# Feynman-Loop

A local-first tool that makes you understand what you learn (and what AI writes for you)
instead of offloading it. You explain a concept in your own words; it finds the gap against
your own source material, asks about the gap instead of answering it, and resurfaces the
concept later so the understanding sticks.

It runs as an MCP connector inside the AI tools you already use (Claude Desktop, Claude Code,
Cursor, ChatGPT Desktop, Gemini), with an optional web UI and CLI.

## Honest summary first

This tool adds friction on purpose. Its whole premise is that struggling to retrieve and
explain something is what makes it stick, and that AI used too early removes that struggle.
That makes it useful for a specific person and a waste of time for everyone else. Read the
"Who this is for" and "Limitations" sections before you install. They are not marketing
hedges; they are the truth about where this helps and where it does not.

## What it is

- **Explain-it-back, grounded in your own material.** The scoring rubric is built from the
  source you provide (a paper, your notes, a file of code), not from generic model knowledge.
- **Gaps come back as questions, never answers.** The point is that you retrieve it.
- **A transfer challenge** tests whether you can apply the idea to a case you were not shown,
  not just restate it.
- **A persistent competence ledger.** It records what you can and cannot explain, over time,
  with due dates. This is the part a stateless chat cannot be.
- **Verified scoring.** Every credited point must carry a verbatim quote from your own words
  that the code checks; scores are computed in code, not asserted by a model. A judge cannot
  grant credit it cannot point to.

## What it is not

- Not a summarizer (that offloads the thinking).
- Not a flashcard app (that is retention without understanding).
- Not gamified. One streak number tracks consistency. No points, no leaderboards.
- Not "AI that does the work for you." It is the opposite of that.

## Who this is for

- Students, bootcampers, career-switchers, and engineers onboarding into a new stack, where
  mastering well-documented, canonical material is genuinely the job.
- People prepping for a moment where they will have to explain their work out loud: an
  interview, a code review, an exam.
- Anyone who ships AI-written code and does not want to be unable to explain it later.

## Who this is not for (be honest with yourself)

- People who want AI to make their work faster and lighter. This makes it slower on purpose.
- Frontier researchers working at the edge of a field. The judge grounds rubrics in a source;
  if the truth of your work lives only in your head and not in any document, it cannot ground
  anything useful. The tool is strongest on canonical material and weakest exactly where deep
  novel expertise lives.

## Limitations (read before installing)

- **It fights a real habit.** Most people, most of the time, want less friction, not more.
  If you will not actually do the reps, this tool will not change that.
- **The "teeth" are opt-in and Claude Code only.** The commit-mode gate (below) that asks you
  to explain shipped code before wrapping up is a Claude Code Stop hook. In Claude Desktop,
  ChatGPT, Cursor, and Gemini there are no such hooks, so there it is pull-only: it helps when
  you invoke it, and stays quiet otherwise.
- **The line-count trigger is a rough proxy.** The shipped-code nudge counts AI-written lines,
  including tests and docs, so it can fire on a large non-code change.
- **Single user, single machine.** No accounts, no sync, no team features. The web UI is
  unauthenticated and bound to localhost.

## How it works

Two speeds:

- **Rapid (default, 2-3 minutes):** say *"quiz me on backprop"*. One sharp question per rubric
  point, a one-line answer from memory each, an instant verdict, a running score.
- **Full:** you explain the whole concept in your own words and it is judged in one pass.

Either way the rubric is grounded in your source, gaps return as questions, a transfer
challenge probes application, and the ledger schedules the concept to come back when due.

## Controlling it: mode and scope

Proactivity is opt-in by design and you choose both how much and where.

```
feynman-loop mode nudge     # default: offer an explain-back at a natural moment, never forced
feynman-loop mode commit    # self-armed gate: at session end, if you shipped unexplained
                            #   AI-written code, you are asked to explain it before wrapping up.
                            #   You can still decline; it fires once and never traps you.
feynman-loop mode off       # silence all proactive surfaces (explicit `feynman-loop due` still works)

feynman-loop scope          # show which projects the proactive hooks fire in (default: all)
feynman-loop scope add .    # only fire in this project (and any others you add)
feynman-loop scope remove . # stop firing here
feynman-loop scope all      # reset to firing everywhere
```

Scope governs the always-on hooks; the MCP tools stay callable in any host where you have
configured the connector. Scope is a convenience, not a security sandbox.

### Project-scoped recall

Spaced recall is scoped to the project you are working in. A concept is filed under the project
(the git repo root) where you first explained it, and a session's due nudge surfaces only that
project's concepts plus a global bucket, so unrelated concepts do not interrupt the wrong project.
Concepts that are not filed under a project are global and surface everywhere.

```
feynman-loop projects                        # audit: every concept grouped by project (+ global)
feynman-loop reproject "Backprop" .          # file a concept under this project (default: cwd)
feynman-loop reproject "Backprop" --global   # send it back to the global bucket
```

A concept gets its project when the host passes the working directory to the check; the
SessionStart hook prompts the host to do this. Concepts you captured before this, or in a session
where the directory was not passed, stay global until you `reproject` them. Re-explaining a concept
in another project never moves it on its own; `reproject` is the explicit, reversible way to move
it (run it again, or with `--global`, to undo).

## Setup

**Terminal (recommended; Claude Code, Cursor, any MCP host, and the proactive hooks):** use an
isolated virtualenv. Do not install into a global or conda environment; the optional vector
stack (numpy, torch) is sensitive to ABI drift.

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

**One-click bundle (Claude Desktop, no terminal):** build it on your own machine with
`./scripts/build_mcpb.sh`, then double-click `dist/feynman-loop.mcpb` (or Claude Desktop >
Settings > Extensions > Install Extension). The API key field is optional; leave it empty for
zero-key mode. Needs Python 3.10+ and Node (for the bundler). The bundle ships native wheels, so
it is platform-specific: build it on the OS you will run it on. A prebuilt bundle is not yet
attached to Releases.

## No API key? It still works

You do not need an API key inside an MCP host. With no `ANTHROPIC_API_KEY` set, the server runs
in **zero-key mode**: the chat model you already pay for does the language work under a strict
protocol, and the server does the integrity work in code:

- every credited verdict must carry a verbatim quote from your explanation, and the server
  verifies the quote actually appears there; credit it cannot find is downgraded
- rubric quotes are verified against your source passages the same way
- every score is computed in code from the verified statuses, never taken from the model
- minimum rubric sizes per depth are enforced, so a lazy one-point rubric is rejected

With a key, an independent judge model builds and scores instead, which is the strongest setup
(your own chat model cannot be talked into leniency). The ledger records which judge scored
every event, so your history stays honest about its own strength. The terminal and web surfaces
call the API directly, so those two do need a key.

## Data, privacy, and security

- **Where your data lives:** one local SQLite ledger (owner-only, `chmod 0600`) plus a markdown
  knowledge-graph vault, under `$FEYNMAN_HOME` (default `~/.feynman-loop`). It stores your
  concept labels, a snapshot of the source material you give it, your verbatim explanations,
  scores, and due dates. It never leaves your machine on its own.
- **Where your data goes:** only judging calls leave the machine. With a key, your source
  passages and explanations go to the Anthropic API under your key. In zero-key mode, the host
  chat model you already use does the judging, so nothing leaves beyond what that host already
  sees. Embeddings run locally. The web UI is localhost. There is no telemetry and no analytics.
- **Secrets:** no API key is stored in this repo or its git history. Your key lives only in your
  host's local config (for example Claude Desktop's config file), which is standard for every
  MCP server. Zero-key mode needs no key at all.
- **What the hooks record:** the shipped-code capture stores file names and line counts only,
  never your code content. Out-of-scope projects are not recorded at all.
- **Known sharp edges:** the web UI is unauthenticated and localhost-only, so anyone with local
  access to your machine could use it and your key; treat it as a personal-machine tool. File
  uploads to the web UI are not size-capped. Both are low risk for single-user local use, and
  are why the web UI is optional and off by default.

## Test

```
.venv/bin/python -m pytest -q
```

## Run the demo (CLI)

Needs the `embeddings` extra and an API key set in the shell only. Never commit the key or
paste it anywhere shared. Prefer `feynman-loop check`, which tells you what is missing instead
of a traceback.

```
export ANTHROPIC_API_KEY=...
.venv/bin/python -m feynman_loop.cli path/to/source.txt "Backpropagation"
```

You type your explanation, then an empty line to submit. It returns the grounded gaps and, if
your explanation is solid, a transfer challenge. Sources can be `.txt` or text-based `.pdf`
(scanned or image-only PDFs are not OCR'd).

## Run the web UI

Needs the `embeddings` extra and an API key.

```
export ANTHROPIC_API_KEY=...
.venv/bin/feynman-loop web --port 8000
```

Open http://localhost:8000, paste your source or upload a `.pdf`/`.txt`, name the concept, and
explain it. The grounded gaps and the transfer challenge render in the browser.

## How the project is governed

Three docs, read in order: `Principles.md` (the constitution), `Context.md` (current state and
the full decision log), `Learnings.md` (guardrails and findings). Contributions are welcome;
the design boundaries in `Principles.md` are deliberate and non-negotiable (no gamification,
no forced interruption, grounding is mandatory).

## License

See `LICENSE`.
