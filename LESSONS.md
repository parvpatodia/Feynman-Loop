# LESSONS (append-only, build-mode)
Read at session start (recent tail) by the SessionStart hook. After any failure, bug,
or Parv correction, append a row. Prune a lesson once promoted into the playbook/contract.

| date | what went wrong | root cause | corrective rule |
|---|---|---|---|
| 2026-06-15 | A correctness-sweep subagent reported a "genuine defect" (MCP tests fail to collect under mcp 1.12.4) that flatly contradicted the in-session smoke run (135 passed). | The subagent ran pytest outside the project `.venv`, against a different `mcp` build. Its env != the project's env. | Before acting on ANY subagent "it's broken" claim, reproduce it in the project's own venv (`source .venv/bin/activate`). A finding that contradicts your own green smoke run is an env artifact until proven otherwise. |
| 2026-06-15 | `derive_profile` asserted "you state better than you apply" by pooling an explain of one concept against a transfer of a *different* concept — a false competence claim shown in the SessionStart nudge + `progress`. | The explain-vs-apply gap was computed over disjoint per-kind event sets, not within-concept. A learner-level generalization was derived from apples-to-oranges evidence. | A learner-level insight in the competence model must be computed from WITHIN-concept evidence (concepts the user did both sides on), never pooled across disjoint concepts. The moat metric must be auditable from the events it claims to summarize. |
