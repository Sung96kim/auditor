"""The events the hooks post: durable in ``spool.jsonl``, signalled in memory (spec 8.1)."""

import json
import os
import threading
from collections.abc import Callable, Iterable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from auditor.graph.refine.models import ClientKind
from auditor.paths import REPO_KEY_PATTERN, spool_path

#: one Stop event carries a whole dirty tree; past this the body is a mistake, not a batch
MAX_EVENT_PATHS = 10_000


class EventKind(StrEnum):
    """Which of spec 8.2's two edit paths produced a batch."""

    EDIT = "edit"
    STOP = "stop"


class Event(BaseModel):
    """One batch of paths one client named, with the clock the daemon accepted it at."""

    model_config = ConfigDict(frozen=True)

    repo: str
    paths: tuple[str, ...] = ()
    kind: EventKind = EventKind.EDIT
    client: ClientKind = ClientKind.CLAUDE_CODE
    session_id: str = ""
    at: float = 0.0


class EventRequest(BaseModel):
    """``POST /events``' body: what the hook posts, before Stage 0 narrows it to an `Event`.

    ``key`` is the caller's own ``repo_dir_key``, which the hook computed anyway; deriving it here
    would be one ``git rev-parse`` per edit event. It is constrained to that hash's own shape,
    because it names the directory the spool is written to.
    """

    model_config = ConfigDict(frozen=True)

    repo: str
    key: str = Field(pattern=REPO_KEY_PATTERN)
    paths: tuple[str, ...] = Field(default=(), max_length=MAX_EVENT_PATHS)
    kind: EventKind = EventKind.EDIT
    client: ClientKind = ClientKind.CLAUDE_CODE
    session_id: str = ""


class Spool:
    """One repo's append-only pending events. The durability half of ``POST /events``."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: Event) -> None:
        """Add one event, creating the repo dir. One line of JSON, so a torn write costs one line."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    def read(self) -> tuple[Event, ...]:
        """Every readable event, oldest first. A torn line is skipped, never raised."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ()
        out: list[Event] = []
        for line in lines:
            try:
                out.append(Event.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                continue
        return tuple(out)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class EventQueue:
    """What the daemon has accepted and not yet consumed, keyed by ``repo_dir_key``.

    The spool is the truth and the in-memory set is the wakeup, so a daemon that dies between the
    202 and the consumer loses nothing: :meth:`drain` reads the file.
    """

    def __init__(self, spool_for: Callable[[str], Path] = spool_path) -> None:
        self._spool_for = spool_for
        self._pending: set[str] = set()
        self._signal = threading.Event()
        self._lock = threading.Lock()
        self._keyed: dict[str, threading.Lock] = {}
        self.accepted = 0

    def spool(self, key: str) -> Spool:
        return Spool(self._spool_for(key))

    def _key_lock(self, key: str) -> threading.Lock:
        """One lock per repo, so two request threads cannot interleave a line."""
        with self._lock:
            return self._keyed.setdefault(key, threading.Lock())

    def put(self, key: str, event: Event) -> None:
        """Append and signal. ``POST /events`` answers 202 after this returns."""
        with self._key_lock(key):
            self.spool(key).append(event)
            with self._lock:
                self._pending.add(key)
                self.accepted += 1
        self._signal.set()

    def adopt(self, keys: Iterable[str]) -> int:
        """Take on spools found on disk at start (spec 8.1's drain). Returns how many."""
        found = [key for key in keys if self._spool_for(key).exists()]
        with self._lock:
            self._pending.update(found)
        if found:
            self._signal.set()
        return len(found)

    def keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._pending))

    def drain(self, key: str) -> tuple[Event, ...]:
        """Every spooled event for one repo, oldest first, taken by rename and then read.

        The rename is what makes a write racing a drain safe: it lands in a fresh spool under the
        live name and keeps its key, so an event that was answered 202 is never unlinked (P26).
        """
        spool = self.spool(key)
        staged = spool.path.with_suffix(".draining")
        with self._key_lock(key):
            try:
                os.replace(spool.path, staged)
            except OSError:
                events: tuple[Event, ...] = ()
            else:
                events = Spool(staged).read()
                staged.unlink(missing_ok=True)
            with self._lock:
                if not spool.path.exists():
                    self._pending.discard(key)
                if not self._pending:
                    self._signal.clear()
        return events

    def wait(self, timeout: float) -> bool:
        """Block until something is pending. The ``threading.Event`` seam spec 15's fixture uses."""
        return self._signal.wait(timeout)

    @property
    def pending_keys(self) -> int:
        """How many repos have an unconsumed spool. Repos, never events (the wire says so too)."""
        return len(self.keys())
