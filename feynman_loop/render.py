"""Render a GapReport for the user (piece 6). Plain text for the CLI demo.

The contract: show what they got right, then each gap with the source quote that grounds it.
Never a bare "you're wrong" — every gap carries its citation.
"""

from __future__ import annotations

from feynman_loop.models import GapReport


def _clean(text: str) -> str:
    # WHY: source chunks keep their original line breaks, so a quote can render split
    # mid-sentence. Collapse runs of whitespace to single spaces for clean display.
    return " ".join(text.split())


def render_gap_report(report: GapReport) -> str:
    lines: list[str] = [f"Understanding: {report.understanding_level:.0%}"]

    if report.correct_points:
        lines.append("\nWhat you got right:")
        lines.extend(f"  + {p}" for p in report.correct_points)

    if report.gaps:
        lines.append("\nGaps (each grounded in your own source):")
        for g in report.gaps:
            lines.append(f"  - {_clean(g.description)}")
            lines.append(f"      source: {g.citation.doc_label}")
            lines.append(f'      "{_clean(g.citation.quote)}"')
    else:
        lines.append("\nNo gaps found against your source.")

    return "\n".join(lines)
