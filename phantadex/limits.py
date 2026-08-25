"""Bounds on how far a run goes, and on how long it may fail to get anywhere.

Two separate questions, kept together because both answer "should this run stop
now?" and both are counted from the same event: the run reaching an item it has
not been on before.

:class:`Budget` is the deliberate bound -- ``--items 5`` means five items and
then stop. :class:`StallGuard` is the involuntary one: a run that keeps
navigating but never arrives anywhere new is cycling, and cycling forever is
what a bounded run is supposed to make impossible.
"""

from . import logs

log = logs.get_logger("limits")

# Ticks the run may spend without reaching an item it has not seen before.
# Generous, because a single item legitimately holds the loop for many ticks
# while a modal is cleared or a handler retries. What it will not tolerate is
# that state continuing indefinitely.
DEFAULT_STALL_TICKS = 40


class Budget:
    """How much of a course a single run is allowed to cover.

    Counted as the run meets items rather than indexed from the start of the
    course, because a run resumes at the first unfinished item: ``--modules 2``
    on a course half done means the next two modules, which is the only reading
    under which the flag does something useful.
    """

    def __init__(self, modules=None, items=None):
        self.modules = modules
        self.items = items
        self.seen_items = []
        self.seen_modules = []

    @property
    def enabled(self):
        """Reports whether any limit was asked for."""
        return self.modules is not None or self.items is not None

    def describe(self):
        """Returns the limit as it appears in the settings line, or ``""``."""
        if self.items is not None:
            return f"limit={self.items} items"
        if self.modules is not None:
            return f"limit={self.modules} modules"
        return ""

    def check_against(self, total_modules, total_items):
        """Validates the limit against the course that was actually loaded.

        A limit larger than the course is not an error -- the run simply ends
        when the items run out -- but it is worth saying out loud, because the
        difference between "5 modules" and "all 4 of them" is invisible
        otherwise.
        """
        if not self.enabled:
            return
        if self.items is not None:
            logs.step(f"limited to the next {self.items} of {total_items} items")
            if self.items > total_items:
                logs.warn(
                    f"The course maps {total_items} items, fewer than the {self.items} "
                    "asked for; the run ends when they are done."
                )
            return
        logs.step(f"limited to the next {self.modules} of {total_modules} modules")
        if self.modules > total_modules:
            logs.warn(
                f"The course maps {total_modules} modules, fewer than the {self.modules} "
                "asked for; the run ends when they are done."
            )

    def admits(self, item_key, module_name):
        """Reports whether the run may handle this item, recording it when it may.

        Called on arrival at an item, before it is handled, so a refusal stops
        the run without touching the item it refused.
        """
        if not self.enabled:
            return True
        if item_key in self.seen_items:
            return True

        if self.items is not None and len(self.seen_items) >= self.items:
            return False
        if (
            self.modules is not None
            # An unmapped item reports no module, and was refused here while
            # being excluded from the tally below -- so a row the map had not
            # caught up with ended a run that still had a module to go.
            and module_name
            and module_name not in self.seen_modules
            and len(self.seen_modules) >= self.modules
        ):
            return False

        self.seen_items.append(item_key)
        # An unmapped item reports no module. Counting it as one would let a
        # page the map does not know about consume a module of the budget.
        if module_name and module_name not in self.seen_modules:
            self.seen_modules.append(module_name)
        return True

    def summary(self):
        """Returns what the run covered, for the line printed when it stops."""
        items = len(self.seen_items)
        modules = len(self.seen_modules)
        item_word = "item" if items == 1 else "items"
        module_word = "module" if modules == 1 else "modules"
        return f"{items} {item_word} across {modules} {module_word}"


class StallGuard:
    """Ends a run that has stopped reaching items it has not already been on.

    The traversal has several ways to spin without progressing: a map that
    offers an item its own successor, an unmapped page with no handler and
    nowhere to advance to, a reload that lands back where it started. Each was
    individually recoverable and none of them was individually bounded, so a run
    that hit one repeated it until somebody noticed.
    """

    def __init__(self, limit=DEFAULT_STALL_TICKS):
        self.limit = limit
        self.visited = set()
        self.idle_ticks = 0

    def observe(self, item_key):
        """Records the item this tick is on. Returns False once the run is stalled.

        Reaching somewhere new resets the count, so the limit bounds an unbroken
        run of ticks that got nowhere rather than the length of the session.
        """
        if item_key and item_key not in self.visited:
            self.visited.add(item_key)
            self.idle_ticks = 0
            return True
        self.idle_ticks += 1
        if self.idle_ticks <= self.limit:
            return True
        log.debug("No new item in %d ticks; the run is not progressing.", self.idle_ticks)
        return False
