"""Structure-aware chunking with overlap (Decision: v1 chunking).

Strategy, in order of preference for where to cut:
  1. Respect the document's own boundaries first: split on blank lines (paragraphs).
  2. If a paragraph is bigger than the cap, fall back to sentence splits.
  3. If a single sentence is STILL bigger than the cap, hard-split it by words (last resort).
  4. Greedily pack these segments into chunks up to `max_words`, and carry `overlap_words`
     from the tail of each chunk into the next so a concept straddling a boundary survives
     in both neighbours.

Sizing is in WORDS, not tokens, on purpose: token counting needs tiktoken or the model's
tokenizer (extra dependency, and the model can change). Words are a dependency-free, good-
enough proxy for a v1 demo. Swap to token sizing later if retrieval quality demands it; this
all lives behind the Retriever interface, so nothing else changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ChunkConfig:
    max_words: int = 200      # cap per chunk
    overlap_words: int = 30   # words carried from one chunk into the next


# WHY: split on one-or-more blank lines. This catches the natural paragraph breaks in papers
# and lecture notes, which is where one idea tends to end and the next begins.
_PARAGRAPH_RE = re.compile(r"\n\s*\n")

# WHY: naive sentence boundary, a period/question/exclamation followed by whitespace and a
# capital letter or quote. Good enough for v1; it will mis-split abbreviations ("Fig. 2"),
# which is an accepted limitation, logged for later.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_RE.split(text) if p.strip()]


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


def _hard_split_words(text: str, max_words: int) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


def _segments(text: str, max_words: int) -> list[str]:
    """Break the document into units no larger than max_words, cutting at the most natural
    boundary available (paragraph, then sentence, then a hard word split)."""
    segs: list[str] = []
    for para in _split_paragraphs(text):
        if len(para.split()) <= max_words:
            segs.append(para)
            continue
        for sent in _split_sentences(para):
            if len(sent.split()) <= max_words:
                segs.append(sent)
            else:
                segs.extend(_hard_split_words(sent, max_words))
    return segs


def _tail_words(text: str, n: int) -> str:
    return " ".join(text.split()[-n:]) if n > 0 else ""


def chunk_document(text: str, config: ChunkConfig | None = None) -> list[str]:
    """Split a document into overlapping, structure-aware chunks."""
    config = config or ChunkConfig()
    segments = _segments(text, config.max_words)

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for seg in segments:
        seg_words = len(seg.split())
        # WHY: if adding this segment would overflow the cap, close the current chunk first,
        # then seed the next chunk with overlap from the tail of the one we just closed.
        if current and current_words + seg_words > config.max_words:
            chunks.append(" ".join(current))
            overlap = _tail_words(chunks[-1], config.overlap_words)
            current = [overlap] if overlap else []
            current_words = len(overlap.split())
        current.append(seg)
        current_words += seg_words

    if current:
        chunks.append(" ".join(current))
    return chunks
