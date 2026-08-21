"""Course progress ledger: maps items to disk paths and tracks archival state."""

import os
import random
import tempfile
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime

from . import config, logs, storage, urls

log = logs.get_logger("course_manager")

ARCHIVABLE_TYPES = ("VIDEO", "READING", "DISCUSSION")
# The content-type suffixes ``save_content`` writes, one per archivable type.
ARCHIVE_CONTENT_TYPES = ("Transcript", "Reading", "Discussion")
# The item type a saved file implies, used when an item reaches the ledger by
# being archived rather than by appearing in the map as an archivable row.
ITEM_TYPES_BY_CONTENT_TYPE = dict(zip(ARCHIVE_CONTENT_TYPES, ARCHIVABLE_TYPES))
UNRESOLVED_TITLE = "Unknown_Item"
UNRESOLVED_MODULE = "Unknown_Module"
LOCK_TIMEOUT_SECONDS = 30
LOCK_POLL_SECONDS = 0.05
LEGACY_TRANSCRIPT_DIR = "coursera_transcripts"
# The ledger indexes what was archived; the archive itself is the ``.txt`` file
# written beside it, which is never truncated. A supplement can embed a PDF
# viewer whose rendered text runs to book length, and storing that inline turns
# the ledger into something slow to parse and impossible to read, so the copy
# the ledger keeps is bounded. The limit sits well above a long reading -- the
# longest seen so far is under 20,000 characters -- so ordinary content is
# stored whole and only outliers are cut.
MAX_LEDGER_CONTENT_CHARS = 40000


class CourseManager:
    """Owns the on-disk archive for one course: text files plus an XML ledger."""

    def __init__(self, course_map, course_name, root_dir=None):
        self.course_map = course_map
        self.course_name = course_name
        self.safe_course_name = storage.sanitize_filename(course_name)
        root_dir = root_dir or config.TRANSCRIPT_DIR
        self.root_dir = os.path.join(root_dir, self.safe_course_name)
        _migrate_legacy_course_directory(root_dir, self.safe_course_name)
        course_slug = next(
            (
                urls.course_slug(url)
                for lessons in self.course_map.values()
                for _title, _item_type, url, _duration in lessons
                if urls.course_slug(url)
            ),
            "course",
        )
        ledger_name = f"{storage.sanitize_filename(course_slug)}.pdex.xml"
        self.xml_path = os.path.join(self.root_dir, ledger_name)
        self.legacy_xml_path = os.path.join(self.root_dir, "course_content.xml")
        self.lock_path = os.path.join(self.root_dir, ".phantadex.lock")

        os.makedirs(self.root_dir, exist_ok=True)
        self._flat_paths = self._flatten_map()
        with self._course_lock():
            self._migrate_legacy_ledger()
            self._init_xml()

    # ------------------------------------------------------------------ setup

    def _flatten_map(self):
        """Returns every mapped item path in course order."""
        return [
            urls.normalize_path(url)
            for _, lessons in self.course_map.items()
            for _, _, url, _ in lessons
        ]

    def _migrate_legacy_ledger(self):
        """Renames the legacy ledger when no Phantadex ledger would be replaced."""
        if not os.path.exists(self.legacy_xml_path):
            return
        if os.path.exists(self.xml_path):
            logs.warn(f"Both legacy and Phantadex ledgers exist; retained {self.legacy_xml_path}")
            return
        os.replace(self.legacy_xml_path, self.xml_path)

    def _init_xml(self):
        """Creates the ledger skeleton, or reconciles an existing one with the map.

        Reconciling matters because the map is rebuilt from the live sidebar on
        every run. A first run against a sidebar that had not finished lazy-
        loading wrote a short ledger, and because the old code returned early
        whenever the file existed, those items never appeared again -- archiving
        them would report ``ledger unmatched`` forever. Items are only ever
        added; nothing already recorded is renamed, reordered, or dropped.
        """
        tree = None
        if os.path.exists(self.xml_path):
            tree = self._read_tree()
            if tree is None:
                # A corrupt ledger reads the same as a missing one. Rebuilding
                # over it would have discarded every archived <content> block,
                # so the unreadable file is set aside and kept instead.
                self._quarantine_ledger()

        if tree is None:
            root = ET.Element("course", name=self.course_name, updated=str(datetime.now()))
        else:
            root = tree.getroot()

        known = {item.get("url") for item in root.findall(".//item")}
        modules = {node.get("title"): node for node in root.findall("module")}
        added = 0

        for module_name, lessons in self.course_map.items():
            module_node = modules.get(module_name)
            for title, item_type, url, _duration in lessons:
                if item_type not in ARCHIVABLE_TYPES:
                    continue
                path = urls.normalize_path(url)
                if path in known:
                    continue
                if module_node is None:
                    module_node = ET.SubElement(root, "module", title=module_name)
                    modules[module_name] = module_node
                item_node = ET.SubElement(module_node, "item")
                item_node.set("title", title)
                item_node.set("type", item_type)
                item_node.set("url", path)
                ET.SubElement(item_node, "content").text = ""
                known.add(path)
                added += 1

        if tree is None:
            self._write_tree(ET.ElementTree(root))
        elif added:
            log.info("Added %d newly discovered item(s) to the ledger", added)
            self._write_tree(tree)

    def _quarantine_ledger(self):
        """Renames an unparseable ledger aside so nothing overwrites it."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantine_path = f"{self.xml_path}.corrupt-{stamp}"
        os.replace(self.xml_path, quarantine_path)
        logs.warn(f"Ledger was unreadable; kept a copy at {os.path.basename(quarantine_path)}")

    def _write_tree(self, tree):
        """Atomically replaces the ledger with a fully serialized XML file."""
        if hasattr(ET, "indent"):
            ET.indent(tree, space="  ", level=0)
        descriptor, temporary_path = tempfile.mkstemp(
            dir=self.root_dir, prefix=".pdex.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                tree.write(handle, encoding="utf-8", xml_declaration=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.xml_path)
        except Exception:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise

    @contextmanager
    def _course_lock(self):
        """Serializes archive mutations across processes on Windows and POSIX."""
        with open(self.lock_path, "a+b") as handle:
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    _lock_file(handle)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out waiting for archive lock: {self.lock_path}"
                        ) from exc
                    # Jitter keeps several waiting processes from waking in lockstep.
                    time.sleep(LOCK_POLL_SECONDS * (1.0 + random.random()))
            try:
                yield
            finally:
                _unlock_file(handle)

    def _read_tree(self):
        """Parses the ledger, returning ``None`` if it is missing or corrupt."""
        if not os.path.exists(self.xml_path):
            return None
        try:
            return ET.parse(self.xml_path)
        except ET.ParseError as exc:
            log.error("Ledger at %s is corrupt: %s", self.xml_path, exc)
            return None

    # -------------------------------------------------------------- resolution

    def resolve_location(self, current_url):
        """Returns ``(module_index, lesson_index, safe_title, module_name)``.

        Indices are 1-based; ``(0, 0, "Unknown_Item", "Unknown_Module")`` means the
        URL is not in the course map.
        """
        module_index = 1
        for module_name, lessons in self.course_map.items():
            lesson_index = 1
            for title, _type, map_url, _duration in lessons:
                if urls.same_item(map_url, current_url):
                    return (
                        module_index,
                        lesson_index,
                        storage.sanitize_filename(title),
                        module_name,
                    )
                lesson_index += 1
            module_index += 1
        return (0, 0, UNRESOLVED_TITLE, UNRESOLVED_MODULE)

    def filename_for(self, current_url, content_type):
        """Builds the archive filename for an item.

        Unresolved items are disambiguated by their Coursera item id. Previously
        every unresolved item collapsed onto ``M00_L00_Unknown_Item_<type>.txt``
        and silently overwrote the one before it.
        """
        module_index, lesson_index, title, _module = self.resolve_location(current_url)
        if module_index == 0:
            suffix = urls.item_id(current_url) or storage.sanitize_filename(
                urls.normalize_path(current_url).replace("/", "_")
            )
            title = f"{UNRESOLVED_TITLE}_{suffix}"
        return f"M{module_index:02d}_L{lesson_index:02d}_{title}_{content_type}.txt"

    # ----------------------------------------------------------------- writing

    def save_content(self, current_url, content_text, content_type):
        """Writes item content to disk and records it in the ledger.

        Returns ``(filename, ledger_updated)``. Distinct content for a path that
        was already archived is kept as a new ``_v2`` file rather than
        overwriting the original.
        """
        with self._course_lock():
            filename = self.filename_for(current_url, content_type)
            written_path = storage.save_versioned(
                os.path.join(self.root_dir, filename), content_text
            )
            written_name = os.path.basename(written_path)
            return written_name, self._update_ledger(
                current_url, content_text, content_type, written_name
            )

    def _update_ledger(self, current_url, content_text, content_type, filename):
        """Stores content against the matching ledger item. Returns success."""
        tree = self._read_tree()
        if tree is None:
            return False

        for item in tree.getroot().findall(".//item"):
            item_url = item.get("url")
            if not item_url or not urls.same_item(item_url, current_url):
                continue
            content_node = item.find("content")
            if content_node is None:
                content_node = ET.SubElement(item, "content")
            content_node.text = _bounded_content(content_text, filename)
            if item.get("status") == "failed":
                for attribute in ("status", "failure_reason", "failed_at"):
                    item.attrib.pop(attribute, None)
            try:
                self._write_tree(tree)
                return True
            except OSError as exc:
                log.error("Could not write ledger %s: %s", self.xml_path, exc)
                return False

        return self._adopt_item(tree, current_url, content_text, content_type, filename)

    def _adopt_item(self, tree, current_url, content_text, content_type, filename):
        """Adds a ledger entry for an archived item the ledger did not list.

        The sidebar row's type and the live page's own classification can
        disagree: a supplement whose title reads as a survey is mapped
        ``FILLER`` and so never reaches the ledger, yet once opened it is a
        reading and is archived. Discussion prompts behave the same way when a
        row renders before its subtext does. The file was written either way,
        and the ledger -- which exists to account for what was archived --
        did not mention it. The item is filed where the map places it, or under
        a catch-all module when the map does not know the URL at all.
        """
        entry = self._map_entry(current_url)
        if entry is None:
            module_name = UNRESOLVED_MODULE
            title = _title_from_url(current_url)
            item_type = ITEM_TYPES_BY_CONTENT_TYPE.get(content_type, "UNKNOWN")
        else:
            module_name, title, mapped_type = entry
            item_type = ITEM_TYPES_BY_CONTENT_TYPE.get(content_type, mapped_type)

        root = tree.getroot()
        module_node = next(
            (node for node in root.findall("module") if node.get("title") == module_name),
            None,
        )
        if module_node is None:
            module_node = ET.SubElement(root, "module", title=module_name)
        item_node = ET.SubElement(module_node, "item")
        item_node.set("title", title)
        item_node.set("type", item_type)
        item_node.set("url", urls.normalize_path(current_url))
        ET.SubElement(item_node, "content").text = _bounded_content(content_text, filename)

        try:
            self._write_tree(tree)
        except OSError as exc:
            log.error("Could not write ledger %s: %s", self.xml_path, exc)
            return False
        log.info("Adopted %s into the ledger under %s", current_url, module_name)
        return True

    def _map_entry(self, current_url):
        """Returns ``(module_name, title, item_type)`` for a mapped URL, else ``None``."""
        for module_name, lessons in self.course_map.items():
            for title, item_type, map_url, _duration in lessons:
                if urls.same_item(map_url, current_url):
                    return module_name, title, item_type
        return None

    def mark_failed(self, current_url, reason):
        """Records that automation gave up on an item. Returns success.

        Without this a run could finish "successfully" while silently leaving
        structurally failed items behind -- the only trace was a transient log
        line, so a later pass could not tell an archived item from a skipped one.
        """
        with self._course_lock():
            tree = self._read_tree()
            if tree is None:
                return False
            for item in tree.getroot().findall(".//item"):
                item_url = item.get("url")
                if not item_url or not urls.same_item(item_url, current_url):
                    continue
                item.set("status", "failed")
                item.set("failure_reason", str(reason)[:200])
                item.set("failed_at", str(datetime.now()))
                try:
                    self._write_tree(tree)
                    return True
                except OSError as exc:
                    log.error("Could not write ledger %s: %s", self.xml_path, exc)
                    return False
            log.debug("No ledger entry matches %s", current_url)
            return False

    def failed_paths(self):
        """Returns the normalized paths automation gave up on."""
        tree = self._read_tree()
        if tree is None:
            return set()
        return {
            item.get("url")
            for item in tree.getroot().findall(".//item")
            if item.get("url") and item.get("status") == "failed"
        }

    # ----------------------------------------------------------------- reading

    def archived_paths(self):
        """Returns the set of normalized paths that already hold content.

        Callers loop over hundreds of items; reading the ledger once here avoids
        the O(n^2) re-parse the archiver used to do per target.
        """
        tree = self._read_tree()
        if tree is None:
            return set()
        return {
            item.get("url")
            for item in tree.getroot().findall(".//item")
            if item.get("url")
            and item.find("content") is not None
            and (item.find("content").text or "").strip()
        }

    def is_archived(self, current_url):
        """Reports whether this item already has ledger content or a disk file."""
        normalized = urls.normalize_path(current_url)
        if any(urls.same_item(path, normalized) for path in self.archived_paths()):
            return True

        for content_type in ARCHIVE_CONTENT_TYPES:
            candidate = os.path.join(self.root_dir, self.filename_for(current_url, content_type))
            if os.path.exists(candidate) and os.path.getsize(candidate) > 20:
                return True
        return False

    def is_mapped(self, current_url):
        """Reports whether ``current_url`` appears in the course map.

        Callers must consult this before reading ``None`` from
        :meth:`get_next_url` as "course complete". The map is rebuilt from the
        live sidebar, so an item missed by a slow lazy-load is absent rather
        than last, and treating the two alike ended runs mid-course.
        """
        normalized = urls.normalize_path(current_url)
        return any(urls.same_item(path, normalized) for path in self._flat_paths)

    def get_next_url(self, current_url):
        """Returns the absolute URL of the next mapped item, or ``None`` at the end.

        ``None`` is also returned for an unmapped URL; use :meth:`is_mapped` to
        tell the two apart.
        """
        normalized = urls.normalize_path(current_url)
        for index, path in enumerate(self._flat_paths):
            if urls.same_item(path, normalized):
                if index + 1 < len(self._flat_paths):
                    return urls.absolute_url(self._flat_paths[index + 1])
                return None
        log.debug("Current URL %s not found in course map", current_url)
        return None

    def get_previous_url(self, current_url):
        """Returns the mapped item before ``current_url``, or ``None`` at the start."""
        normalized = urls.normalize_path(current_url)
        for index, path in enumerate(self._flat_paths):
            if urls.same_item(path, normalized):
                if index > 0:
                    return urls.absolute_url(self._flat_paths[index - 1])
                return None
        log.debug("Current URL %s not found in course map", current_url)
        return None


def _bounded_content(content_text, filename):
    """Returns the ledger's copy of archived text, cut to a readable length.

    Text past :data:`MAX_LEDGER_CONTENT_CHARS` is dropped and replaced with a
    pointer to the file that holds all of it. The cut lands on the last line
    break inside the budget when there is one, so the stored excerpt ends on a
    whole line rather than mid-sentence.
    """
    text = content_text or ""
    if len(text) <= MAX_LEDGER_CONTENT_CHARS:
        return text
    excerpt = text[:MAX_LEDGER_CONTENT_CHARS]
    break_at = excerpt.rfind("\n")
    if break_at > 0:
        excerpt = excerpt[:break_at]
    return (
        f"{excerpt.rstrip()}\n\n"
        f"[Truncated at {MAX_LEDGER_CONTENT_CHARS} characters of {len(text)}. "
        f"The full text is in {filename}.]"
    )


def _title_from_url(current_url):
    """Derives a readable title from a Coursera item slug."""
    slug = urls.normalize_path(current_url).rsplit("/", 1)[-1]
    title = slug.replace("-", " ").strip()
    return title.title() if title else UNRESOLVED_TITLE


def _migrate_legacy_course_directory(root_dir, safe_course_name):
    """Moves one legacy default course directory without merging or overwriting."""
    if os.path.abspath(root_dir) != os.path.abspath(config.TRANSCRIPT_DIR):
        return
    legacy_course_dir = os.path.join(LEGACY_TRANSCRIPT_DIR, safe_course_name)
    destination = os.path.join(root_dir, safe_course_name)
    if not os.path.exists(legacy_course_dir):
        return
    if os.path.exists(destination):
        logs.warn(
            f"Both legacy and Phantadex archive directories exist; retained {legacy_course_dir}"
        )
        return
    os.makedirs(root_dir, exist_ok=True)
    try:
        os.replace(legacy_course_dir, destination)
    except FileNotFoundError:
        if not os.path.exists(destination):
            raise


def _lock_file(handle):
    """Acquires one non-blocking byte/file lock using the host stdlib."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle):
    """Releases the lock acquired by :func:`_lock_file`."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
