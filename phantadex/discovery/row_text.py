"""Reads a sidebar row's text with its line breaks intact.

Coursera ships the outline minified, with no whitespace between tags, and
``innerText`` on an element that is *not being rendered* is defined to fall
back to ``textContent``. A collapsed module's rows are not rendered, so the
row's title and its subtext arrive concatenated: "Module 4 OverviewReading.
Duration: 20 minutes". Callers split on newlines to separate the two, so the
title swallowed the subtext and the subtext -- the signal that tells a graded
row from an ungraded one sharing the same URL segment -- came back empty.

Reconstructing the breaks from the element tree works in either state, which
is why it is preferred over reading ``innerText`` and repairing the result.
"""

from .. import logs

log = logs.get_logger("discovery.row_text")

# Mirrors how `innerText` lays out a rendered row: text accumulates across
# inline boxes and breaks at every block boundary. Children the page hides
# outright are skipped, which is also what `innerText` does with them.
_ROW_LINES_JS = r"""
el => {
    const lines = [];
    let run = '';
    const flush = () => {
        const text = run.trim();
        if (text) lines.push(text);
        run = '';
    };
    const visit = (node) => {
        for (const child of node.childNodes) {
            if (child.nodeType === Node.TEXT_NODE) {
                run += child.textContent;
                continue;
            }
            if (child.nodeType !== Node.ELEMENT_NODE) continue;
            const display = getComputedStyle(child).display;
            if (display === 'none') continue;
            const inline = display.startsWith('inline') || display === 'contents';
            if (!inline) flush();
            visit(child);
            if (!inline) flush();
        }
    };
    visit(el);
    flush();
    return lines.join('\n');
}
"""


def read_lines(element):
    """Returns the element's text, one line per block, or ``""`` if unreadable."""
    try:
        return (element.evaluate(_ROW_LINES_JS) or "").strip()
    except Exception as exc:
        log.debug("Row text read failed: %s", exc)
        return ""
