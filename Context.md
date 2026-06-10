# CONTEXT.md — current state & where we left off

> Read this after `PRINCIPLES.md`. This is the living state of the project. Update it as
> decisions are made. Do not relitigate anything in the Decision Log — the reasoning is recorded.

## What this project is (one paragraph)

An AI tool for thought that makes its user understand more deeply instead of offloading
cognition. Its core interaction is **explain-it-back**: the user explains a concept in
their own words, and the system finds the gap between their explanation and a trusted
source, then probes only that gap. It maintains a persistent model of the user's
understanding and resurfaces concepts when due. First user is Parv, on his own coursework
and papers. See `PRINCIPLES.md` for the full philosophy.

## The spec as it stands

- **Core loop:** user adds a concept (tied to a live goal) → system asks them to explain it
  → system judges the explanation against the source of truth → surfaces the gap and probes
  it → records what the user does/doesn't understand → resurfaces later when due.
- **Source of truth (priority):** (1) user's uploaded material, (2) curated retrieval
  corpus (RAG), (3) base model knowledge as flagged fallback. No fine-tuning in v1.
- **Trigger (v1):** user turns it on + scheduler marks concepts due; user chooses when to
  engage. No forced interruption, no screen-watching in v1. Editor hook is later.
- **Progress:** a visible map of concepts the user can explain without gaps. No points/streaks.

## Decision Log (settled — do not relitigate)

1. **Thesis / problem** — AI that makes you think better, grounded in cognitive-offloading
   evidence (MIT "Your Brain on ChatGPT"). Villain is *frictionless AI used too early*.
2. **Wedge** — the anti-NotebookLM. Summarizers offload cognition; we refuse to summarize.
3. **Atom** — the single concept.
4. **Core interaction** — explain-it-back (generation effect + Feynman + desirable difficulty).
5. **More than a prompt** — defensibility is memory-of-understanding + proactivity +
   discipline. The chatbot layer is a commodity; do not let it become the product.
6. **Relevance vs. timing** — relevance is filtered at *intake* (only seed goal-linked
   concepts); timing is the *user's hand* in v1 (they choose when to engage what's due).
   This resolves the conflict between pure time-based spaced repetition and "only quiz me
   on what's relevant to my work."
7. **Source of truth** — layered priority above. **RAG for facts, model for judgment.**
   No fine-tuning in v1 (fine-tuning teaches behavior not facts; goes stale; can't cite;
   still hallucinates).
8. **No gamification** — points measure compliance, punish productive wrongness, and crowd
   out intrinsic motivation. Progress = gap-free-concept map; skipping just stays due.
9. **Concept schema (data model, part 1)** — a `concept` stores a *locator* to its source of
   truth, not the truth text. Locator avoids staleness (same reason fine-tuning was killed)
   and lets retrieval pull the passage relevant to what the user actually said. Fields:
   `id` (stable internal key, never the name), `label` (display only), `goal_id` (FK to
   relevance-link), `source_ref { tier: uploaded|corpus|model_fallback, doc_id (null if
   model_fallback), doc_label, retrieval_query }`, `created_at`. Granularity handled by a
   stored `retrieval_query` + live RAG at judge time, NOT stored page spans. One source per
   concept, tagged with `tier` to preserve the trust ordering; automatic fallback chains
   (upload→corpus→model) are retrieval-code logic, not schema, and are deferred from v1.
10. **user-state schema (data model, part 2)** — tracks one `(user, concept)` pair over time.
    Fields: `concept_id`, `user_id`, `last_explanation`, `identified_gaps[]`,
    `understanding_level` (0..1), `last_reviewed_at`, `next_due_at`, `review_count`.
    **Due policy = HYBRID:** a spacing interval modulated by the last review's understanding
    (clean explanation → interval grows; gap found → interval shrinks/resets). Captures both
    the forgetting curve and Principle 4 ("measure understanding, not activity").
    **`next_due_at` is written ONLY at the end of a review**, by the interval logic. The
    scheduler only READS it to propose candidates ("N due") and never mutates it. "Due" is a
    suggestion the system makes, never a state it imposes (Decision 6: timing is the user's
    hand in v1; no forced interruption).
11. **relevance-link schema (data model, part 3)** — **many-to-many.** A `goal` record
    {`id`, `user_id`, `label`, `type` (exam|project|paper|other), `status` (active|archived),
    `created_at`}; deadline deferred from v1. A `relevance_link` join table {`concept_id`,
    `goal_id`} ties concepts to goals. **`goal_id` is removed from `concept`** (supersedes
    that part of Decision 9); the link owns the tie, so a concept can serve multiple goals
    without duplication. **Scheduler filter:** a concept is a due-candidate only if it links
    to >=1 goal with `status == active`. Archiving a goal makes its concepts go quiet (not
    deleted, not penalized) — Principle 6, natural consequence not punishment.
12. **Measurement (Principle 4) = DELAY + TRANSFER, not score-trajectory.** Understanding is
    measured by (a) DELAY: can the user explain the concept cold after the spacing interval,
    with no source in front of them; and (b) TRANSFER: can they handle a variation/application
    they were not shown. REJECTED: tracking `understanding_level` trajectory or "re-explain the
    same thing better" — circular (the judge grading itself) and gameable (Goodhart; the user
    just bolts on the feedback). Key nuance: the metric is made ungameable by TASK DESIGN
    (delay removes the source crutch; novelty removes the memorized-phrasing crutch), NOT by
    swapping out the judge — the judge still scores the answer. OPEN/heavy: TRANSFER requires
    the system to GENERATE a novel variation and ground its correct answer in a source. That is
    new build surface; confirm whether transfer is in v1 or deferred behind delay.
13. **v1 demo scope (Principle 5).** Beat: user explains a concept they're confident about;
    system surfaces, grounded in the user's OWN uploaded source, the specific gap they didn't
    know they had. The shock is the gap in their explanation, NOT a generated question (that
    would be the question-generator interaction we did not choose). Demo = 7 pieces: (1) ingest
    one doc → chunk + embed → vector index; (2) seed one concept with locator + minimal
    goal/link; (3) user types explanation; (4) RAG retrieves the passage via the concept's
    `retrieval_query`; (5) judge compares explanation vs retrieved passage → structured
    `GapReport`; (6) UI shows gap + correct points + grounding quote; (7) persist to user-state.
    CUT from demo: scheduler/resurfacing/DELAY (can't elapse live), TRANSFER (heavy), proactive
    pop-up (demo just opens it), multi-goal/auth. Output contract `GapReport` scaffolded in
    `feynman_loop/models/gap_report.py` (Citation mandatory per the trust design; affirm correct
    points, not just punish; no auto follow-up question in v1).

## Open decisions

- **Data model** — DONE. `concept` (D9), `user-state` (D10), `relevance-link` (D11). Written as code in `feynman_loop/models/`.
- **Measurement** — how the system and user know understanding improved (not just activity).
- **v1 scope** — smallest version that proves the thesis on Parv's own material.

## NEXT TASK (resume here)

Data model is fully locked (D9–D11) and written as Pydantic models in `feynman_loop/models/`.

All four planning decisions are now settled:
- **Measurement** — DONE (Decision 12): delay + transfer.
- **v1 scope** — DONE (Decision 13): the single explain-it-back beat, 7 pieces.

**Decision 14 — stack & infra philosophy (settled):**
- Judge = **Anthropic Claude** (Parv has a key; reliable; clean structured output via tool use).
- Embeddings = **local model** (sentence-transformers/BGE). Anthropic has NO first-party
  embeddings API, and local keeps the demo key-free. Swappable behind the retriever interface.
- Vector store = simplest **local impl (Chroma)** for the demo.
- Surface = **CLI first** to prove the loop, minimal **web UI** for the actual demo.
- REFRAME on "production from the start": building pgvector/cloud now for a single-user,
  single-doc demo is the over-engineering Principles §7 forbids. The durable way to avoid
  throwaway work is **Dependency Inversion** (the OOP-D rule): orchestration depends on the
  `Retriever` and `Judge` interfaces; concrete impls swap with zero rewrite. Interfaces live in
  `feynman_loop/retrieval/base.py` and `feynman_loop/judge/base.py`. Production-shaped contracts
  now; production cost deferred until earned.

**Build progress:**
- Data model + GapReport contract: DONE, tested.
- `Retriever` / `Judge` interfaces (DIP): DONE.
- Pieces 1 + 4 (ingest + retrieve): DONE, tested. Chunking = structure-aware + overlap,
  word-sized (`feynman_loop/retrieval/chunking.py`). Retriever = `ChromaRetriever` with
  INJECTED embeddings, in-memory Chroma (`feynman_loop/retrieval/chroma_store.py`). Default
  embedder = local `all-MiniLM-L6-v2`, lazy-loaded.
- Piece 5 (judge): DONE, tested. `ClaudeJudge` (`feynman_loop/judge/claude_judge.py`),
  model `claude-opus-4-8`, structured output via `messages.parse` + Pydantic. Two grounding
  guarantees: judges ONLY against passages (Decision 15; abstains/raises on empty retrieval),
  and the model references passages by INDEX so it can never hallucinate a citation's
  doc_id/doc_label — the code maps index → real identifiers. 10 tests green total.

- Pieces 2, 3, 6, 7 (orchestration loop): DONE, tested. `run_review` in `feynman_loop/loop.py`
  wires retrieve (by `source_ref.retrieval_query`) → judge → write user-state. `next_due_at` is
  computed at the END of the review by `scheduling.compute_next_due` (hybrid: u=0→1 day,
  u=1→30 days). Persistence = JSON (`storage.JsonUserStateStore`, Decision 14). Render =
  `render.render_gap_report`. CLI entry = `feynman_loop/cli.py` (`python -m feynman_loop.cli`).
  16 tests green total.

**ALL 7 demo pieces built.** The explain-it-back loop is end-to-end runnable from the CLI.

**Transfer measurement (Decision 12) built.** Realizes the "apply, don't restate" half of measurement.
- `models/transfer.py`: `RubricPoint` (criterion + grounded Citation), `TransferProbe` (question
  + rubric), `TransferResult` (transfer_score + met/missed).
- `transfer/claude_transfer.py` (`ClaudeTransfer`): generates a novel application question + a
  rubric where every point is grounded in a passage by INDEX (same anti-hallucination trick as
  the judge); refuses to ask if nothing grounds. Scores the answer against the fixed rubric →
  fraction met. Both calls: opus-4-8, messages.parse, adaptive thinking.
- `user_state.transfer_level` (separate from understanding_level; restate vs apply).
- `loop.generate_transfer_probe` + `loop.score_transfer`; `TRANSFER_GATE = 0.6` (transfer only
  fires after a solid explanation). CLI wires it after explain-it-back.
- Offline tests cover wiring, grounding, scoring math.

**Post-transfer follow-up (A+B, decided 2026-06-09).** After a transfer is scored:
- A: the grounded missed points are shown, and `score_transfer` recomputes `next_due_at` from
  `min(understanding_level, transfer_score)` (weakest-link: you don't own a concept until you can
  both state AND apply it, so a weak transfer pulls it back soon). `REMEDIATION_GATE = 0.6`.
- B: if transfer < gate, the system offers ONE narrower retry (`generate_remediation` targets the
  missed principle, same index-grounding), bounded to a single attempt (`_Session.remediation_done`).
- 31 tests green. Live generate/score/remediation Claude paths still need a real run to confirm.

**PDF ingest (built 2026-06-09).** `feynman_loop/sources.py` `extract_text(filename, data)` dispatches
.pdf (pypdf, text-based; raises a clear error on scanned/image-only PDFs, no OCR in v1) vs .txt.
CLI `load_source(path)` handles both; web adds `POST /api/session/upload` (multipart) + a file input
in the UI, reusing `_start_session`. Extracted text flows through the same chunk→embed→judge pipeline.
Deps: pypdf, python-multipart. Real extraction verified with a generated PDF. 35 tests green.

**Rubric-grounded scoring (post-demo fix #1, 2026-06-09).** Replaced the judge's holistic
"understanding %" (a sticky model guess) with rubric scoring: `build_concept_rubric` derives the
concept's key points from the source ONCE at setup (stored on `Concept.rubric`); every review
scores the explanation against those FIXED points (met/partial/missed), and understanding_level
is computed IN CODE from the statuses — accurate, responsive (cover a point → it rises), and
consistent across attempts. Anti-gaming: gaps are PROBES (questions), never the missing fact
verbatim; the scorer does not credit near-verbatim copying of the source; the ungameable mastery
measure stays TRANSFER. `Judge` interface = `build_rubric` + `evaluate(concept, explanation)`
(no passages at review time). `RubricPoint` moved to `gap_report.py` (shared). 37 tests green.
NOTE: live "does it feel accurate" behavior needs a real-key run; offline tests verify the logic.

**Post-demo progress:** #1 rubric scoring DONE. #2 voice input DONE (browser SpeechRecognition,
Chrome/Edge). #3 latency: deferred transfer-probe generation out of /api/review into
`/api/transfer/generate` (gap returns after one model call; the challenge loads while the user
reads) + run_review no longer retrieves. Remaining latency levers, opt-in with tradeoffs: prompt
caching (small inputs → modest), model tiering to Sonnet (accuracy tradeoff, Parv's call),
streaming the gap.
#4 UI polish DONE (Linear/Vercel-style pass, progress bars, score animations).

**#5 distribution (the MCP swing, confirmed):**
- Tier-3 no-source fallback DONE: start with just a concept (no upload) and the rubric/transfer
  are built from the model's own knowledge, flagged "general knowledge (unverified)" in the UI
  (`grounded: false`). Unified code path: no source → retriever=None → empty passages → knowledge
  mode in judge + transfer. `MODEL_FALLBACK_LABEL` in gap_report. 39 tests green.
- MCP server DONE (`feynman_loop/mcp_server.py`, FastMCP, stdio). 5 tools: start_check
  (source_text = context you're in, or empty → tier-3), judge_explanation (gaps as probes),
  make_transfer, score_transfer (one bounded remediation), progress (memory/what's-due view).
  Reuses the whole pipeline via `loop_ops`; user-state persists in JSON so progress works over
  time. Host-answer mitigation: every tool result instructs the host to relay, not answer. Run:
  `python -m feynman_loop.mcp_server`. 43 tests green (offline; live host calls need a real run).
- Connectors + curated corpus: roadmap/pitch only, not built.

**Status: all post-demo items (#1-#5) shipped.** Web app (polished, voice, PDF, tier-3) + MCP
server. Live MCP test on 2026-06-10 ran the full loop end-to-end (NBA: 50%→67%→transfer 29%→
remediation) with the host correctly relaying probes.

**Decision 16 — the product IS the understanding ledger (2026-06-10).** Brutal-honesty review
found the memory layer did not persist (per-process user id; concepts in RAM only), meaning the
moat was fake and "why not just prompt Claude?" had no answer. Fixed and extended:
- `JsonIdentity` (stable local user id), `JsonConceptStore` (concepts + rubrics persisted;
  re-explaining a concept attaches to its existing history by normalized-label lookup, instant
  start, no rubric rebuild), `progress` reads from DISK and survives restarts (explicit test).
- **Learner ledger** (`feynman_loop/learner.py`): append-only `ReviewEvent` log (every explain/
  transfer outcome + missed criteria), failure-mode tagging via Haiku (6-tag taxonomy: mechanism/
  purpose/structure/application/distinction/context), and `derive_profile` (computed in code):
  explain-vs-apply gap, recurring weak modes, insight string. Exposed in `progress.learner`.
  Web + MCP share ONE identity and ledger. 52 tests green.
- Positioning vs "Claude already learns your persona": Claude's memory is a personality model
  (style/preferences); this is a COMPETENCE model (scored evidence of what you can explain, with
  due dates). That distinction is the defensible thing.

**Open direction (Parv to ratify): the wedge + proactivity.** Proposed wedge: engineers/students
who ship AI-written work they can't explain, with the bite moments (interview, review, exam) as
the trigger. Form: capture+check via MCP in the work tool; PROACTIVITY via Claude Code hooks
(e.g. session-start "N concepts due", post-session "you shipped X lines you didn't write —
explain-back?") instead of a widget (rejected: nobody opens another app) or screen-watching
(ruled out long ago). MCP alone is pull-only; hooks are the honest path to push."

**Remaining to actually demo:**
- Run it LIVE: `export ANTHROPIC_API_KEY=...`, then
  `python -m feynman_loop.cli <source.txt> "<Concept>" "<retrieval query>"`. First run downloads
  the local embedding model (all-MiniLM-L6-v2).
- Web UI (deferred per Decision 14; CLI proves the loop first).
- Open implementation calls still pending Parv: confirm the hybrid interval shape (linear vs
  SM-2 ease_factor), and whether transfer measurement (Decision 12) enters v1 or stays deferred.

Also still open (deferred implementation calls surfaced during the data model pass):
- **Storage layer** — Pydantic models exist, but no DB chosen yet (SQLite vs Postgres).
- **App framework / surface** — the pop-up + chat surface and the "turn it on + scheduler"
  trigger are not built; framework not chosen.
- **Hybrid interval detail** — confirm whether the spacing interval is recomputed each review
  from `understanding_level` (no stored ease field) or needs an SM-2-style stored ease factor.

**Process (per the contract):** Parv owns the load-bearing decisions. The agent synthesizes
confirmed decisions into code and scaffolds, but does not make the design calls for him.
EOF
