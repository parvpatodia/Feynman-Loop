# PRINCIPLES.md

> The constitution for this project. Any AI agent (Claude Code, Cursor, etc.) reads this
> at the start of every session and works by it. The human (Parv) owns all direction;
> the agent does the research, scaffolding, and mundane work — never the thinking.
> Version 0.2.

---

## 1. Mission

Build AI that makes its user **think better**, not think less. The dominant paradigm
optimizes for frictionless answers, which offloads cognition. We optimize for the
opposite: engagement, retrieval, and understanding that lasts.

The recursion that defines this project: **the product's philosophy and the way we
build it are the same thing.** If the way the agent works with Parv would make him a
passive vibe-coder, it has betrayed the product. Make him sharper, or you are wrong.

## 2. The problem (evidence, not vibes)

- MIT Media Lab, *Your Brain on ChatGPT* (Kosmyna et al., 2025): EEG evidence that
  early reliance on LLMs reduces neural engagement and produces "cognitive debt."
- The nuance is the design principle: the villain is **frictionless AI used too early**.
  AI applied *after* the brain has worked the material can *help* thinking.
- Core rule: **struggle first, assist second.**

## 3. Product philosophy (design principles)

1. **Interrogate, don't answer.** Default to the question that makes the user retrieve
   and reason, not the summary that lets them skip it.
2. **Struggle first, assist after.** Friction is the feature.
3. **Surface the metacognition gap.** Show where understanding *actually* breaks.
4. **Measure understanding, not activity.** If we can't tell whether the user got
   smarter (vs. busier), we haven't built the product.
5. **One thing, relentlessly.** No modes/features until the single core interaction earns them.
6. **Relevance is filtered at intake.** A concept only enters the system because it is
   tied to a live goal (a paper, a project, an exam). The scheduler never has to guess
   what the user cares about, because the user only seeded things they care about.

## 4. What makes this MORE THAN A PROMPT (the moat)

A Socratic system prompt is a commodity the model labs will ship as a toggle. This
project is defensible only via what a prompt fundamentally cannot do:

- **Memory of the user's mind.** A persistent model of what they grasped, faked, and got
  wrong — plus resurfacing logic. A knowledge state, not a conversation.
- **Proactivity.** It detects a checkable moment and surfaces the right concept; the user
  doesn't have to remember to engage.
- **Discipline / commitment device.** It refuses to fold when the user is tired and
  demands the easy answer.

**Engineering discipline:** keep the LLM the *smallest possible commodity component* — a
judge/reasoner you call. The assets you OWN are the state model, the curated retrieval
corpus, the resurfacing logic, and the trust design. If effort drifts into prompt-tuning
or fine-tuning the judge instead of building the layer around it, you have drifted back
into wrapper territory. That is the line to watch.

## 5. Source of truth (how the judge knows "correct")

The eval is only as good as what it's judged against. Layered priority:

1. **User's uploaded material first** (the paper, the lecture notes) — highest authority,
   because it's the truth the user is actually tested on.
2. **A curated retrieval corpus second** — indexed references for the field. The asset we own and grow. (RAG.)
3. **Base model's own knowledge last** — fallback for canonical concepts only, flagged
   lower-confidence so a course-specific answer is never overruled by the model's generic one.

**RAG for facts, the model for judgment.** No fine-tuning in v1: fine-tuning teaches
behavior/style, not facts; it goes stale, can't cite, and still hallucinates. Retrieval
supplies the truth; the model supplies the reasoning. (HPC/A100 fine-tuning is justified
only if the *judging behavior itself* later needs tuning — not for facts.)

## 6. Anti-goals (what we will NOT build)

- Not a summarizer (that is NotebookLM — the cognitive-offloading machine).
- Not a flashcard app (that is Anki — retention without understanding).
- Not a passive second brain (that is Obsidian/Notion).
- Not gamified. **No points/streaks.** Points measure compliance, punish productive
  wrongness, and crowd out intrinsic motivation. Progress is a *visible map of concepts
  the user can explain without gaps*. A skipped concept is not penalized — it simply
  stays due (natural consequence, not punishment).
- Not "AI that does the thinking." Ever.

## 7. How the agent works WITH Parv (anti-vibe-coder contract)

- Parv decides: direction, system design, architecture, models, dataflow, security.
- The agent does: research, scaffolding, boilerplate, repetitive/mundane work.
- The agent **explains so Parv understands**, and has him reason through hard calls
  rather than handing him answers to copy. Explain before you implement.
- The agent pushes back, is brutally honest, and names risks plainly.
- Production-grade *judgment*, scoped to *shippable*. No over-engineering.
- **Never repeat a mistake twice:** log critical bugs/findings to `LEARNINGS.md`; read it
  at session start, append at session end.

## 8. Decisions — LOCKED

- **Thesis:** AI that makes the user think better, not less.
- **Wedge:** the anti-NotebookLM — bring-your-own-material, refuses to summarize.
- **Atom:** the single concept.
- **First user:** Parv himself (the serious learner who fears getting shallower).
- **The spine:** proactive resurfacing of concepts the user needs, not a reactive chatbot.
- **Core interaction:** EXPLAIN-IT-BACK. The user explains the concept in their own words
  first; the system finds the gap between what they said and the source of truth, and
  probes only there.
- **Target level of understanding:** set by the user, ideally inferred from a piece of
  their own work they upload (evidence, not a vague self-rating).
- **Source of truth:** the layered priority in Section 5. RAG, not fine-tuning, in v1.
- **Trigger model (v1):** user turns it on + a scheduler decides what's *due*; the user
  decides *when* to engage (open it, or act on a quiet "N concepts due" nudge). No forced
  mid-flow interruption in v1. Editor-hook detection is a later version.
- **No gamification** (see Section 6).

## 9. Decisions — OPEN (Parv's to make next)

- **The data model** (current task): the `concept`, `user-state`, and `relevance-link`
  schemas. See `CONTEXT.md` → Next Task.
- **The measurement:** how the system (and the user) knows understanding improved.
- **Scope of v1:** the smallest version that proves the thesis on Parv's own material.
