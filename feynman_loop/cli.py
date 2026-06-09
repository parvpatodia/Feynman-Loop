"""Minimal CLI for the explain-it-back demo (Decision 14: CLI first, web later).

Usage:
    export ANTHROPIC_API_KEY=...
    python -m feynman_loop.cli path/to/source.txt "Backpropagation" "backprop: chain rule"

It ingests the source, seeds one concept whose locator points at it, asks you to explain the
concept in your own words, then retrieves + judges + renders the grounded gap.
"""

from __future__ import annotations

import sys
from uuid import uuid4

from feynman_loop.judge.claude_judge import ClaudeJudge
from feynman_loop.loop import run_review
from feynman_loop.models import Concept, SourceRef, SourceTier
from feynman_loop.render import render_gap_report
from feynman_loop.retrieval.chroma_store import ChromaRetriever, sentence_transformer_embedder
from feynman_loop.storage import JsonUserStateStore


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2

    source_path, concept_label, retrieval_query = argv[1], argv[2], argv[3]
    text = open(source_path, encoding="utf-8").read()
    doc_id = uuid4()
    doc_label = source_path

    # ingest the one source document (chunk -> embed -> index)
    retriever = ChromaRetriever(embed=sentence_transformer_embedder())
    retriever.ingest(doc_id=doc_id, doc_label=doc_label, text=text)

    # seed the concept; its locator points at the doc we just ingested
    concept = Concept(
        label=concept_label,
        source_ref=SourceRef(
            tier=SourceTier.UPLOADED,
            doc_id=doc_id,
            doc_label=doc_label,
            retrieval_query=retrieval_query,
        ),
    )

    print(f"\nExplain '{concept_label}' in your own words. End with an empty line.\n")
    explanation = "\n".join(iter(lambda: sys.stdin.readline().rstrip("\n"), ""))

    report, state = run_review(
        concept=concept,
        user_id=uuid4(),
        explanation=explanation,
        retriever=retriever,
        judge=ClaudeJudge(),
        store=JsonUserStateStore("feynman_state.json"),
    )

    print("\n" + render_gap_report(report))
    print(f"\nNext review due: {state.next_due_at:%Y-%m-%d} (review #{state.review_count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
