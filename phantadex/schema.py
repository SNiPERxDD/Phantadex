"""Access layer over the selector schema.

Three sources feed it, in priority order:

1. Discovery-verified selectors from the **user state file** -- what the last
   discovery run proved against the live page. Repairs made after a Coursera
   markup change reach the runner from here.
2. ``config.yaml`` shipped inside the package -- immutable defaults that travel
   with the wheel.
3. ``ELEMENTS_SCHEMA`` in :mod:`phantadex.element_schema` -- the hand-maintained
   fallback list, tried last.

Learned state is deliberately kept out of the installed package: package
directories can be read-only in a normal wheel installation, and an upgrade or
reinstall would otherwise wipe every repaired selector. Layering also means a
stale state file degrades to the shipped defaults instead of breaking the run.
"""

import os
import sys

from . import logs
from .element_schema import ELEMENTS_SCHEMA

log = logs.get_logger("schema")

# Immutable package data, resolved from this file so wheels and editable
# installs read the same shipped defaults regardless of working directory.
PACKAGED_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

STATE_DIR_ENV = "PHANTADEX_STATE_DIR"
STATE_FILE_NAME = "verified_selectors.yaml"

_overrides_cache = None


def state_dir():
    """Returns the user-writable directory holding discovery-learned state.

    ``PHANTADEX_STATE_DIR`` overrides the platform default, which is
    ``%LOCALAPPDATA%`` on Windows, ``~/Library/Application Support`` on macOS,
    and ``$XDG_STATE_HOME`` (or ``~/.local/state``) elsewhere.
    """
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return os.path.abspath(os.path.expanduser(override))

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "phantadex")


def state_config_path():
    """Returns the full path of the discovery-verified selector state file."""
    return os.path.join(state_dir(), STATE_FILE_NAME)


def _schema():
    """Returns the packaged element schema."""
    return ELEMENTS_SCHEMA


def _read_yaml_mapping(path):
    """Returns the mapping stored at ``path``, or ``{}`` when unusable."""
    try:
        import yaml

        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        return loaded if isinstance(loaded, dict) else {}
    except FileNotFoundError:
        log.debug("No selector config at %s", path)
    except Exception as exc:
        log.debug("Could not read selector config %s: %s", path, exc)
    return {}


def verified_selectors():
    """Returns learned state layered over the shipped defaults, category by category."""
    global _overrides_cache
    if _overrides_cache is not None:
        return _overrides_cache

    merged = {
        category: dict(elements)
        for category, elements in _read_yaml_mapping(PACKAGED_CONFIG_PATH).items()
        if isinstance(elements, dict)
    }
    for category, elements in _read_yaml_mapping(state_config_path()).items():
        if isinstance(elements, dict):
            merged.setdefault(category, {}).update(elements)
    _overrides_cache = merged
    return _overrides_cache


def reload_verified_selectors():
    """Drops the cache so a fresh discovery run's output is picked up."""
    global _overrides_cache
    _overrides_cache = None
    _reported_stale.clear()


def _verified_for(category, element_name):
    """Returns the verified selectors for one element as a list (possibly empty)."""
    entry = verified_selectors().get(category, {})
    if not isinstance(entry, dict):
        return []
    value = entry.get(element_name)
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


def selectors_for(category, element_name):
    """Returns the selectors to try for one element, verified ones first."""
    try:
        defaults = _schema().get(category, {}).get(element_name, {}).get("selectors", [])
    except Exception as exc:  # discovery module missing or malformed
        log.debug("Schema lookup failed for %s.%s: %s", category, element_name, exc)
        defaults = []

    ordered = []
    for selector in list(_verified_for(category, element_name)) + list(defaults):
        if selector and selector not in ordered:
            ordered.append(selector)
    return ordered


# A selector can match several nodes where only a later one is on screen;
# scanning is bounded so a pathological match set cannot stall the caller.
MAX_VISIBILITY_SCAN = 20


def first_visible(page, category, element_name):
    """Returns the first *visible* locator for a schema element, else ``None``.

    Every match is examined rather than only ``.first``: Coursera renders
    duplicate controls inside collapsed wrappers, and taking the first DOM node
    reported "not visible" while a usable one sat further down the list.
    """
    for selector in selectors_for(category, element_name):
        try:
            matches = page.locator(selector)
            for index in range(min(matches.count(), MAX_VISIBILITY_SCAN)):
                candidate = matches.nth(index)
                if candidate.is_visible():
                    return candidate
        except Exception as exc:
            log.debug("Selector %r failed for %s.%s: %s", selector, category, element_name, exc)
            continue
    return None


def state_selectors():
    """Returns only the learned state, without the packaged defaults beneath it.

    :func:`verified_selectors` is the right view for *resolving* a selector.
    This one is for rewriting the state file: persisting the merged view would
    freeze the shipped defaults into user state, where they would shadow every
    later upgrade of the packaged config.
    """
    return {
        category: dict(elements)
        for category, elements in _read_yaml_mapping(state_config_path()).items()
        if isinstance(elements, dict)
    }


# Elements already reported stale this run. The hint is worth saying once and
# is noise every time after that: a course with forty videos whose transcript
# markup has moved would otherwise print it forty times.
_reported_stale = set()


def report_stale(element_name):
    """Warns, once per run, that an element's markup no longer matches.

    Every shipped selector missing is the signal that Coursera has changed the
    page, not that this item is unusual. A run cannot repair that itself -- it
    reads selectors, it does not learn them -- so it names the element and
    points at the pass that can.
    """
    if element_name in _reported_stale:
        return
    _reported_stale.add(element_name)
    logs.warn(f"no selector matched {element_name}; the markup has changed -- run 'pdex discover'")
