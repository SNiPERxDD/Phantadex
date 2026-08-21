"""Identity of the page currently open: course, module, item.

Answers "where am I" by reading the sidebar, which is far more accurate than
the page title. Depends on :mod:`rules` for its labels and on nothing else in
the package, so the map builder and the prober can both use it.
"""

from .. import logs, urls

log = logs.get_logger("discovery.context")

UNKNOWN_MODULE = "Unknown Module"

# Walks up from the active sidebar link to the nearest heading above it. Used
# only when the cached map and the breadcrumbs both come up empty.
_NEAREST_HEADING_JS = """(el) => {
    let sidebar = el.closest('nav') || document.querySelector('div.rc-ModuleOutline') || document;
    let headers = Array.from(sidebar.querySelectorAll(
        'button.cds-AccordionHeader-button, h1, h2, h3, h4, [role="heading"]'));
    let elRect = el.getBoundingClientRect();
    let best = null;
    let minDiff = Infinity;
    for (let h of headers) {
        let hRect = h.getBoundingClientRect();
        if (hRect.top < elRect.top) {
            let diff = elRect.top - hRect.top;
            if (diff < minDiff) { minDiff = diff; best = h; }
        }
    }
    return best ? best.innerText.split('\\n')[0].trim() : 'Course Overview';
}"""


def _href_matches(href, current_url):
    """Reports whether a sidebar href addresses the page currently open.

    Delegates to :func:`phantadex.urls.same_item`. Raw substring containment
    matched any item whose slug was a prefix of another ("intro" against
    "intro-part-2"), which attached the wrong module name to the archived item.
    """
    if not href or "/learn/" not in href:
        return False
    return urls.same_item(href, current_url)


def _active_sidebar_link(page, current_url):
    """Returns the sidebar link for the open item, or ``None``."""
    for link in page.locator("a[href*='/learn/']").all():
        if _href_matches(link.get_attribute("href"), current_url):
            return link
    return None


def _split_row(link):
    """Splits a sidebar row into ``(title, subtext)``."""
    lines = [line.strip() for line in link.inner_text().strip().split("\n") if line.strip()]
    if not lines:
        return "Unknown Item", ""
    return lines[0], " | ".join(lines[1:])


def _module_from_cached_map(course_map, current_url):
    """Looks the module up in a course map built earlier in the session.

    The map arrives as an argument rather than through a cache hung off the
    mapping function, so this module stays readable on its own and the caller
    decides how long a map stays trustworthy.
    """
    if not course_map:
        return None
    for module_name, lessons in course_map.items():
        for _title, _type, href, _duration in lessons:
            if _href_matches(href, current_url):
                return module_name
    return None


def _module_from_breadcrumbs(page):
    """Reads the module from the breadcrumb trail's second-to-last crumb."""
    try:
        crumbs = page.locator("[aria-label*='readcrumb'] li").all()
        if len(crumbs) < 2:
            return None
        text = crumbs[-2].inner_text().strip()
        if text and len(text) > 3 and "course" not in text.lower():
            return text
    except Exception as exc:
        log.debug("Breadcrumb module lookup failed: %s", exc)
    return None


def _module_from_nearest_heading(page, link):
    """Falls back to whichever sidebar heading sits closest above the link.

    Uses ``Locator.evaluate``, which hands the matched element to the script as
    its first argument. The previous form passed the *Locator* to
    ``page.evaluate`` as a plain argument; Playwright cannot serialise a Locator,
    so this strategy raised every time and the module fell through to
    "Unknown Module" whenever the cached map and breadcrumbs both missed.
    """
    try:
        return link.evaluate(_NEAREST_HEADING_JS)
    except Exception as exc:
        log.debug("Sidebar JS traversal failed: %s", exc)
        return None


def _resolve_module(page, link, current_url, course_map=None):
    """Names the module owning the active item, most precise strategy first."""
    for strategy in (
        lambda: _module_from_cached_map(course_map, current_url),
        lambda: _module_from_breadcrumbs(page),
        lambda: _module_from_nearest_heading(page, link),
    ):
        name = strategy()
        if name:
            return name
    return UNKNOWN_MODULE


def get_page_metadata(page, course_map=None):
    """Returns ``(module, item_title, subtext)`` for the item currently open.

    Resolved from the sidebar rather than the page title, which is far more
    accurate; the title is only the last-resort fallback. ``course_map`` is the
    outline from the current session, when one has already been built.
    """
    try:
        current_url = page.url.split("?")[0].split("#")[0]
        link = _active_sidebar_link(page, current_url)
        if link is not None:
            item_title, subtext = _split_row(link)
            return _resolve_module(page, link, current_url, course_map), item_title, subtext
    except Exception as exc:
        log.debug("Page metadata lookup failed: %s", exc)

    return UNKNOWN_MODULE, page.title().split("|")[0].strip(), ""


def get_robust_course_name(page):
    """Tries various selectors to find the Course Name."""
    selectors = [
        "a[title*='Home Page']",
        "a.cds-150",
        "a.cds-341.css-yrq2q5",
        "nav[aria-label='Breadcrumbs'] li:first-child",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                text = loc.inner_text().strip() or loc.get_attribute("title")
                if text and len(text) > 2:
                    return text.replace("\n", " ")
        except Exception as exc:
            log.debug("Course-name strategy failed: %s", exc)
    return None
