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
- **An adversarial review (9 angles, verified per finding) beats reading your own diff.** The
  2026-06-10 xhigh review of the zero-key/rapid/progression run found, confirmed live, and fixed:
  (1) judge_explanation could double-log one attempt mid-volley (now refused); (2) the daily
  notification died silently on ANY non-ASCII or control character because json.dumps escapes
  \uXXXX, which AppleScript cannot parse — found only because a verifier actually executed
  osascript (write escaping for the TARGET language, and test the real binary); (3) the .mcpb
  manifest's optional-key template could leave a literal ${user_config...} in env and silently
  select independent mode — has_api_key() now rejects blank/placeholder values; (4) streaks were
  bucketed on UTC days, which breaks nightly-evening users in western timezones — a streak is a
  human-day concept, bucket on LOCAL days, store UTC; (5) the verdict->score fold had drifted
  into three copies (judge, zero-key, rapid) — now ONE loop.fold_verdicts, because three copies
  of scoring math is three ways for modes to disagree; (6) web/CLI lacked the source snapshot
  the MCP surface had (parity restored): when a moat property ships, grep every surface for it.
  Three findings were REFUTED only because guards already existed — keep writing guards.
- **State machines need a default-deny posture.** The zero-key protocol guarded each submit_*
  tool's own phase but let the START of a new step (judge_explanation, make_transfer,
  score_transfer) clobber an in-flight one, silently discarding locked text. Every entry point
  now refuses when ANY step is pending. Also: a retry budget that a caller can re-arm
  (make_transfer resetting remediation_done) is not a budget; bind bounds to the session, not
  the request.
- **A learner-level insight must be auditable from the events it summarizes.** `derive_profile`
  claimed "you state concepts better than you apply them" by comparing the average of ALL explain
  events against the average of ALL transfer events — across DIFFERENT concepts. A user who
  explained concept A well and gave a weak transfer on concept B was told they can't apply, with
  no within-concept evidence for it. That is a false signal in the competence model (the moat) and
  a trust breach (one false "you're wrong" is unrecoverable; the docstring itself promised the
  insight is "auditable from the events"). Fix: restrict both sides of the gap to concepts the
  user did BOTH on. Rule: when a metric generalizes about the learner, compute it from paired
  within-concept evidence, never by pooling disjoint slices that happen to share an axis.
- **A proactive nudge must feed the loop, or it is just guilt.** The "you shipped ~N AI-written
  lines without an explain-back" Stop-hook nudge was a dead-end: it named files but gave the host
  no action, so it never became an actual explain-back. Bridged it in `due.py`: the SessionStart
  context now tells the host to READ the shipped file and pass it as `source_text` to `start_check`,
  grounding the check in the very code shipped, then have the learner explain it. The server still
  stores no code (privacy property, Decision 20); the host, which already has the file, reads it
  live. Still an OFFER at a natural boundary, never a gate (trust criterion). Rule: every proactive
  surface must terminate in a concrete, low-friction entry into the core loop, not a notice.
- **Coercion is only legitimate when self-armed, and "never trap the user" is the reliability bar.**
  The middle path between the gentle nudge (low adoption) and a forced gate (violates the trust
  criterion) is a mode the USER selects: `feynman-loop mode commit` arms a one-shot Stop-hook gate;
  the default stays `nudge` (offer, never force), and `off` silences everything. A self-armed gate
  must STILL be impossible to weaponize against its owner: it blocks the stop exactly once (the
  per-session tally is cleared before any mode branch), it honors Claude Code's `stop_hook_active`
  flag so a continuation chain can't loop, and EVERY error path (garbage stdin, corrupt settings)
  exits 0 and degrades to `nudge` — a bad file can never produce a surprise block. The mode lives
  in one module (`settings.py`); the stdlib-only hook duplicates the constants and a test pins them
  equal, because a silent literal divergence is exactly what the pending-path bug was.
- **Smoke-test the real serialization, not a hand-typed approximation.** A first live smoke of the
  commit gate printed exit 0 (looked broken) only because shell `echo` turned `\n` inside a JSON
  string into raw newlines -> invalid JSON -> capture.py fail-silently wrote nothing. The CODE was
  fine; the harness lied. Feed hooks the same way the host does (json.dumps via a real serializer),
  or a broken smoke will either fake a failure or, worse, fake a pass.
- **Proactivity is opt-in AND opt-WHERE.** Beyond the mode (how proactive), the user picks which
  projects the hooks fire in: `feynman-loop scope add <dir>` makes an allowlist; outside it the
  hooks record nothing and say nothing (the cwd comes from the hook payload). Scope is a
  CONVENIENCE, not a security boundary: an unknown cwd fails OPEN (a parse hiccup must never
  silently mute the loop), and the gating is per-project, not a sandbox. The match check is
  duplicated in the stdlib hooks; a parity test pins it to settings.path_in_scope.
- **"Exposed key" claims must be verified against git history, not assumed.** CONTEXT said "rotate
  the exposed key first." A `git log --all -S "sk-ant-"` showed the only matches in all history are
  the placeholders `sk-ant-` and `sk-ant-real` (example/test text) — no real key was ever committed.
  The key lives only in the local Claude Desktop config (plaintext, standard for every MCP server).
  Verify a suspected leak with `git log -S`/`-p` before treating it as a breach or scrubbing history.
