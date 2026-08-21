"""Filename sanitation and content-aware file writes."""

import os
import re

from . import logs

log = logs.get_logger("storage")

_ILLEGAL_CHARS = re.compile(r'[\\/*?:"<>|\r\n\t]')
_WHITESPACE_RUN = re.compile(r"\s+")

# Windows refuses these names for a file, with or without an extension, because
# they address devices rather than paths. The rule is applied on every platform
# so an archive written on macOS or Linux stays openable once copied to Windows.
_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


def sanitize_filename(name, max_length=None):
    """Strips path-hostile characters and trims to a filesystem-safe length."""
    if max_length is None:
        max_length = 120 if os.name == "nt" else 200
    cleaned = _ILLEGAL_CHARS.sub("", str(name)).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = cleaned[:max_length].strip("._ ")
    if cleaned.split(".", 1)[0].upper() in _DEVICE_NAMES:
        cleaned = f"{cleaned}_"
    return cleaned or "Untitled"


def save_versioned(filepath, new_content):
    """Writes ``new_content``, preserving prior distinct versions as ``_v2``, ``_v3``...

    Deduplication compares normalised text -- identical words in identical
    order -- rather than a similarity ratio, so any genuine edit, however small,
    lands on disk as a new version while a re-extraction that differs only in
    layout does not. This keeps the returned path and the ledger entry
    describing the same content.

    Returns the path actually written, or the existing path when the content
    was already stored verbatim.
    """
    parent = os.path.dirname(filepath)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if not os.path.exists(filepath):
        with open(filepath, "w", encoding="utf-8") as handle:
            handle.write(new_content)
        return filepath

    if _is_duplicate(filepath, new_content):
        return filepath

    base, ext = os.path.splitext(filepath)
    counter = 2
    while True:
        candidate = f"{base}_v{counter}{ext}"
        if not os.path.exists(candidate):
            log.info("Content changed. Saving new version: %s", os.path.basename(candidate))
            with open(candidate, "w", encoding="utf-8") as handle:
                handle.write(new_content)
            return candidate
        if _is_duplicate(candidate, new_content):
            return candidate
        counter += 1


def _normalize(content):
    """Reduces content to its words so layout alone is not a new version.

    Page extraction is not byte-stable between runs: the same reading yields
    different indentation, blank-line runs, and table cell spacing depending on
    when the DOM settled. Every whitespace run collapses to a single space, so
    two extractions of identical text compare equal while a single changed word
    still registers as a new version.
    """
    return _WHITESPACE_RUN.sub(" ", str(content)).strip()


def _is_duplicate(path, new_content):
    """Reports whether ``path`` already holds exactly ``new_content``."""
    try:
        with open(path, encoding="utf-8") as handle:
            existing = handle.read()
    except OSError as exc:
        log.debug("Could not read %s for comparison: %s", path, exc)
        return False
    return _normalize(existing) == _normalize(new_content)
