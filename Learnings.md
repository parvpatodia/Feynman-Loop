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

- **Never ground truth in the user's own past explanation.** Tempting to check a new
  explanation against a previous "correct" one, but that calcifies the user's own errors:
  a subtly-wrong explanation that slipped through once becomes the standard forever. The
  user's prior explanations are `user-state` (do they still know it? did they regress?),
  never `concept` source-of-truth (what is correct?). Keep the two buckets clean.
- **Test the moat AS a moat, before building features on it.** We shipped UI polish, voice, and
  PDF parsing on top of a memory layer that did not survive a server restart (per-process user
  uuid + in-memory concept registry), and nobody noticed until we asked "why would anyone use
  this over Claude chat". The moat property (cross-session persistence) now has an explicit test
  (`test_memory_survives_a_server_restart`). Rule: when the product's defensibility is a property
  (persistence, grounding, privacy), write the test for the property itself, not just the features.
- **Store a locator, not the truth.** A `concept` points at where its truth lives (doc id +
  retrieval query), it does not copy the truth text in. Copying re-creates the staleness
  problem that killed fine-tuning, and freezes one passage so the judge can't match what
  the user actually said. Retrieve live at judge time.
EOF
