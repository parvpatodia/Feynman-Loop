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

## Open decisions

- **Data model** — current task (below).
- **Measurement** — how the system and user know understanding improved (not just activity).
- **v1 scope** — smallest version that proves the thesis on Parv's own material.

## NEXT TASK (resume here)

First pass on the **data model**. The memory-of-understanding layer must store enough to
(a) judge whether an explanation has a gap and (b) know when to resurface. Three buckets:

- **`concept`** — what it needs to know to tell if the user is wrong about it. Includes,
  at minimum: an identifier, and a **source-of-truth reference** (which uploaded doc or
  corpus query the judge retrieves from — this field follows directly from Decision 7).
- **`user-state`** — what it tracks about the user on that concept over time (e.g. last
  explanation, identified gaps, understanding level, due date).
- **`relevance-link`** — how a concept stays tied to the goal that justified its intake
  (Decision 6).

**Process (per the contract):** Parv drafts the schema first — partial/wrong is fine. The
agent then shows where it holds and where it breaks. The agent does NOT write the schema for him.
EOF
