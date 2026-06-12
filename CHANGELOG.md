# Changelog

## 0.1.0 (unreleased)

The first complete system. Explain-it-back with verified scoring, persistent memory, and a
knowledge graph, inside whatever AI chat you already use.

- Explain-it-back core: fixed depth-aware rubrics (overview/working/expert) grounded in your
  own source; score computed in code from per-point statuses; gaps returned as questions,
  never answers; transfer challenges gate mastery, with one bounded remediation retry.
- Evidence-verified judging: every credited verdict must quote the learner's own words and the
  code verifies the quote; applies to the independent API judge AND zero-key mode.
- Zero-key mode: no API key needed in MCP hosts; the host model judges under a strict protocol
  (rubric sizes enforced, quotes verified, scores computed in code, provenance recorded).
- Rapid mode: the 2-minute volley; one question per rubric point, one-line answers, instant
  per-point verdicts, same scoring math and ledger as a full explanation.
- Persistent understanding ledger: SQLite (WAL, owner-only perms), restart-proof grounding via
  capped source snapshots, one-time legacy JSON migration, `feynman-loop export` backup.
- Knowledge graph: Obsidian-compatible markdown vault plus a mermaid map in any MCP host;
  statuses earned from memory intervals; related-concept frontier.
- Proactivity: Claude Code hooks (session-start due questions, shipped-code nudges), macOS
  notifications carrying an actual 30-second question, opt-in daily launchd agent.
- Progression: streak (local calendar days), level-ups, milestones, journey cards. No points,
  no leaderboards, by design.
- Surfaces: MCP server (7+ tools), web UI (localhost, voice input, PDF), terminal CLI; one
  shared ledger. One-click Claude Desktop bundle via `scripts/build_mcpb.sh`.
- Engineering: 130+ offline tests, CI (full matrix + core-install + dependency audit), optional
  `[embeddings]` extra keeps the default install slim.
