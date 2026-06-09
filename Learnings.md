# LEARNINGS.md

> Read at session start. Append at session end. The point: no mistake or settled debate
> happens twice. Two sections — design guardrails (conceptual, established in planning) and
> build learnings (technical, added during development).

## Design guardrails already established (do not relitigate)

- **Don't reach for fine-tuning when the problem is facts, not behavior.** Use RAG. Fine-
  tuning goes stale, can't cite, and still hallucinates. (Settled when choosing source of truth.)
- **Don't add gamification.** Points/streaks measure compliance, punish productive
  wrongness, and crowd out intrinsic motivation. Progress is a gap-free-concept map.
- **Don't build a screen-watcher / vibe-coding classifier in v1.** It's a research problem,
  a privacy nightmare (Microsoft Recall got torched), and false positives destroy trust.
  Use turn-it-on + scheduler in v1; a scoped editor hook (observable diff/paste events) later.
- **Don't merge four products into one.** Every time scope expanded (pop-up + chat + screen-
  adaptive move + monitoring), the right move was to cut to one interaction done well.
- **Don't let the LLM become the product.** Keep it the smallest commodity component (a
  judge you call). Effort goes into owned assets: state model, corpus, resurfacing, trust.
- **The trust criterion is load-bearing:** an interruption is only welcome if the timing is
  right (user-controlled in v1) and the concept is genuinely relevant (filtered at intake).
  One false "you're wrong" breaks trust permanently — grounding the judge is non-negotiable.

## Build learnings (append technical findings here as you develop)

- _(none yet)_
EOF
