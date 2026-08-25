"""Minimal Playwright and console stand-ins for offline tests."""

import io
import logging
from contextlib import contextmanager, redirect_stdout

from phantadex import logs


@contextmanager
def capture_console():
    """Captures real Phantadex log and direct stdout output without ANSI colour."""
    stream = io.StringIO()
    logs.setup()
    logger = logging.getLogger(logs.LOGGER_NAME)
    old_handlers = logger.handlers[:]
    old_level = logger.level
    old_propagate = logger.propagate
    old_colour = logs.paint.enabled
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logs.ConsoleFormatter())
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logs.paint.enabled = False
    try:
        with redirect_stdout(stream):
            yield stream
    finally:
        logs.paint.enabled = old_colour
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate


class FakeLocator:
    """A locator whose match count, visibility and text are fixed up front."""

    def __init__(
        self,
        count=0,
        visible=True,
        text="",
        attributes=None,
        tag="div",
        children=None,
        events=None,
        links=None,
    ):
        self._count = count
        self._visible = visible
        self._text = text
        self._attributes = attributes or {}
        self._tag = tag
        # ``[label, url]`` pairs, as the reading-link walk reads them.
        self._links = links or []
        # Nested `selector -> FakeLocator` map, so a locator can stand in for a
        # container the code scopes further lookups inside (e.g. a modal).
        self.children = children or {}
        self.events = events
        self.clicked = 0
        # Arguments of the last call, so a test can assert how the click was
        # issued (jitter position, forced or not, bounded timeout).
        self.click_kwargs = {}
        self.hover_kwargs = {}

    @property
    def first(self):
        return self

    def nth(self, _index):
        return self

    def count(self):
        return self._count

    def is_visible(self):
        return self._count > 0 and self._visible

    def is_enabled(self):
        return True

    def inner_text(self):
        return self._text

    def get_attribute(self, name):
        return self._attributes.get(name)

    def evaluate(self, script):
        # The row-text walk reads the element's own text, the reading-link walk
        # its anchors; every other script the package sends an element asks for
        # its tag name.
        if "childNodes" in script:
            return self._text
        if "a[href]" in script:
            return [list(link) for link in self._links]
        return self._tag

    def locator(self, selector, has_text=None):
        key = f"{selector}|{has_text}" if has_text is not None else selector
        return self.children.get(key, FakeLocator(count=0))

    def all(self):
        return [self] * self._count

    def click(self, **kwargs):
        if self.events is not None:
            self.events.append("click")
        self.clicked += 1
        self.click_kwargs = kwargs

    def hover(self, **kwargs):
        self.hover_kwargs = kwargs

    def bounding_box(self):
        return {"x": 0, "y": 0, "width": 100, "height": 20}


class FakeMouse:
    """Records pointer operations at the Playwright mouse boundary."""

    def __init__(self, events):
        self.events = events
        self.moves = []

    def move(self, *args, **_kwargs):
        self.events.append("move")
        self.moves.append(tuple(args))

    def wheel(self, *_args, **_kwargs):
        self.events.append("wheel")


class FakePage:
    """A page that resolves selectors from a dict of ``selector -> FakeLocator``."""

    def __init__(
        self,
        url="/learn/course/lecture/abc/item",
        title="Item | Coursera",
        locators=None,
        evaluate_result=None,
        evaluate_error=None,
        wait_error=None,
    ):
        self._url = url
        self._title = title
        self.locators = locators or {}
        self.evaluate_result = evaluate_result
        self.evaluate_error = evaluate_error
        self.evaluated = []
        self.wait_error = wait_error
        self.waited_for = []
        self.goto_calls = []
        self.reloaded = 0
        self.pointer_events = []
        self.mouse = FakeMouse(self.pointer_events)

    @property
    def url(self):
        return self._url

    @url.setter
    def url(self, value):
        """Lets a test simulate the page navigating on its own."""
        self._url = value

    def title(self):
        return self._title

    def locator(self, selector, has_text=None):
        key = f"{selector}|{has_text}" if has_text is not None else selector
        if key in self.locators:
            return self.locators[key]
        return FakeLocator(count=0)

    def wait_for_selector(self, selector, **kwargs):
        """Records a wait and answers it from the same table ``locator`` reads."""
        self.waited_for.append((selector, kwargs))
        if self.wait_error is not None:
            raise self.wait_error
        return self.locator(selector)

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)
        self._url = url

    def reload(self):
        self.reloaded += 1

    def evaluate(self, script, *_args):
        self.evaluated.append(script)
        if self.evaluate_error is not None:
            raise self.evaluate_error
        return self.evaluate_result

    def is_closed(self):
        return False
