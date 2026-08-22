"""Normalisation of scraped course text.

The transcript panel and reading body are read with ``inner_text()``, which
returns the DOM exactly as rendered -- including markup that exists for screen
readers rather than for readers. Archiving that verbatim produced files where
roughly a third of the lines were interface furniture.

Cleaning happens at extraction, so every caller -- traversal, archiver, ledger,
dedup -- sees the same normalised text.
"""

import re

# Coursera precedes each transcript phrase with a button whose label is
# "Play video starting at ::5 and follow transcript". It is an affordance, not
# content, and it repeats the timestamp that already appears on its own line.
_CUE_BUTTON = re.compile(r"^\s*Play video starting at\b.*$", re.IGNORECASE)

# A bare cue timestamp: 0:05, 12:03, 1:02:33.
_TIMESTAMP = re.compile(r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\s*$")

# Zero-width space (U+200B) prefixes nearly every phrase; non-breaking space
# (U+00A0) separates them. Both survive into the file and break naive search.
_INVISIBLE = str.maketrans({"​": "", "‌": "", "‍": "", "﻿": "", "\xa0": " "})


def _normalise_line(line):
    """Strips invisible characters and collapses runs of whitespace."""
    return re.sub(r"[ \t]+", " ", line.translate(_INVISIBLE)).strip()


def clean_transcript(text, keep_timestamps=True):
    """Returns the transcript with interface furniture removed.

    Timestamps are kept by default: they are the only way to locate a phrase in
    the source video, and dropping them would be a lossy decision the caller
    should make explicitly.
    """
    if not text:
        return text

    lines = []
    for raw in text.splitlines():
        if _CUE_BUTTON.match(raw):
            continue
        line = _normalise_line(raw)
        if not line:
            continue
        if _TIMESTAMP.match(line):
            if not keep_timestamps:
                continue
            # A cue with no phrase after it carries nothing; drop the previous
            # timestamp rather than emitting two in a row.
            if lines and _TIMESTAMP.match(lines[-1]):
                lines[-1] = line
                continue
        lines.append(line)

    while lines and _TIMESTAMP.match(lines[-1]):
        lines.pop()

    return "\n".join(lines)


def clean_reading(text):
    """Returns the reading body with invisible characters normalised."""
    if not text:
        return text
    lines = [_normalise_line(raw) for raw in text.splitlines()]
    # Collapse runs of blank lines to a single paragraph break.
    out = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()
