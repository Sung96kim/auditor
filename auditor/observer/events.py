"""The events the hooks post: durable in ``spool.jsonl``, signalled in memory (spec 8.1)."""

import contextlib
import json
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from auditor.graph.refine.models import ClientKind
from auditor.paths import REPO_KEY_PATTERN, spool_path

#: one Stop event carries a whole dirty tree; past this the body is a mistake, not a batch.
#: `auditable_shape` runs once per path on the request thread at about 42 us each, so this is
#: also the ceiling on how long one hook waits: about 84 ms measured, against 456 ms at 10,000
MAX_EVENT_PATHS = 2_000
#: how many batch ids one repo remembers, so a delivery whose answer the client never saw is
#: dropped rather than assessed twice. Two Stop batches a second would still take two minutes
#: to roll one out, and the client's own spool is capped well below it.
REMEMBERED_BATCHES = 256
#: `auditr_observer.spool_name`'s glob: one file per client-written batch, so delete-on-2xx is a
#: single unlink and the daemon's own `spool.jsonl` is never renamed out from under a writer
CLIENT_SPOOL_GLOB = "spool.client.*.jsonl"


def _written_at(path: Path) -> float:
    """One spool file's mtime, so the oldest batch reaches the consumer first."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


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
    #: the client's own id for this batch; "" is a batch from before the id existed and never
    #: deduplicates, which is the safe direction
    batch: str = ""
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
    #: the client spools this batch before it posts it, so the same batch can arrive twice when
    #: the answer outruns the client's socket budget; this is how the second copy is recognised
    batch: str = ""


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
        """Every readable event, oldest first. A torn line is skipped, never raised.

        Decoded with ``errors="replace"`` because a kill can tear a line mid-character, and a
        `UnicodeDecodeError` out of here would propagate all the way out of the daemon's loop.
        """
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ()
        out: list[Event] = []
        for line in lines:
            try:
                out.append(Event.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                continue
        return tuple(out)


class EventQueue:
    """What the daemon has accepted and not yet consumed, keyed by ``repo_dir_key``.

    The spool is the truth and the in-memory set is the wakeup, so a daemon that dies between the
    202 and the consumer loses nothing: :meth:`drain` reads the file and leaves it staged until
    :meth:`consumed` says the batch was taken.
    """

    def __init__(self, spool_for: Callable[[str], Path] = spool_path) -> None:
        self._spool_for = spool_for
        self._pending: set[str] = set()
        self._signal = threading.Event()
        self._lock = threading.Lock()
        self._keyed: dict[str, threading.Lock] = {}
        #: the last `REMEMBERED_BATCHES` batch ids per repo, newest last
        self._seen: dict[str, OrderedDict[str, None]] = {}
        #: every event this daemon has taken; `/api/status` reports the drained half of it
        self.accepted = 0

    def spool(self, key: str) -> Spool:
        return Spool(self._spool_for(key))

    def staged(self, key: str) -> Path:
        """Where :meth:`drain` parks a batch until its consumer has seen it."""
        return self._spool_for(key).with_suffix(".draining")

    def client_spools(self, key: str) -> tuple[Path, ...]:
        """Every batch the hook client wrote for this repo and no daemon has taken yet.

        One file per batch (`auditr_observer.spool_name`), because the client writes it *before*
        it posts and deletes it when the daemon answers: a shared append-only file cannot be
        edited that way, and :meth:`drain` renames the daemon's own out from under any writer.
        """
        directory = self._spool_for(key).parent
        try:
            return tuple(sorted(directory.glob(CLIENT_SPOOL_GLOB), key=_written_at))
        except OSError:
            return ()

    def _client_staged(self, key: str) -> tuple[Path, ...]:
        directory = self._spool_for(key).parent
        try:
            return tuple(
                sorted(directory.glob("spool.client.*.draining"), key=_written_at)
            )
        except OSError:
            return ()

    def _remember(self, key: str, events: Iterable[Event]) -> tuple[Event, ...]:
        """Drop every batch this repo has already drained, and remember the rest.

        The client writes its copy before it posts and deletes it on the answer, so a batch that
        is both in this daemon's own spool and in a file the client left behind is one delivery
        whose answer outran the client's socket budget, never two edits. The daemon's copy is
        read first, so it is the one that survives; dropping the second silently is what keeps
        the largest Stop batches from being assessed twice (spec 8.1, amended).

        Only :meth:`drain` records: a `put` that recorded here would make the daemon's own copy
        the duplicate and drop the delivery entirely.
        """
        with self._lock:
            seen = self._seen.setdefault(key, OrderedDict())
            kept: list[Event] = []
            for event in events:
                if event.batch and event.batch in seen:
                    continue
                if event.batch:
                    seen[event.batch] = None
                kept.append(event)
            while len(seen) > REMEMBERED_BATCHES:
                seen.popitem(last=False)
        return tuple(kept)

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

    def forget(self, key: str) -> None:
        """Stop offering this repo's spool without reading or deleting it.

        What the daemon does with a spool spec 8.2's gate refuses: the files stay on disk for a
        daemon whose gate answers differently, and nothing here builds a loop for them (H2).
        """
        with self._lock:
            self._pending.discard(key)
            if not self._pending:
                self._signal.clear()

    def adopt(self, keys: Iterable[str]) -> int:
        """Take on spools found on disk at start (spec 8.1's drain). Returns how many.

        A ``spool.draining`` counts: it is a batch a killed predecessor answered 202 for and
        never handed to a consumer, and :meth:`drain` reads it before the fresh spool.
        """
        found = [
            key
            for key in keys
            if self._spool_for(key).exists()
            or self.staged(key).exists()
            or self.client_spools(key)
            or self._client_staged(key)
        ]
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
        A batch already staged by a killed predecessor is read first and the fresh spool stays
        pending, so the older events reach the consumer in front of the newer ones.
        """
        spool = self.spool(key)
        staged = self.staged(key)
        with self._key_lock(key):
            if not staged.exists():
                # nothing to take when no spool sits under the live name
                with contextlib.suppress(OSError):
                    os.replace(spool.path, staged)
            for written in self.client_spools(key):
                with contextlib.suppress(OSError):
                    os.replace(written, written.with_suffix(".draining"))
            events = Spool(staged).read()
            for held in self._client_staged(key):
                events += Spool(held).read()
            with self._lock:
                if not spool.path.exists() and not self.client_spools(key):
                    self._pending.discard(key)
                if not self._pending:
                    self._signal.clear()
        return self._remember(key, events)

    def consumed(self, key: str) -> None:
        """Drop the batch :meth:`drain` staged. Until this runs, a restart adopts it again."""
        with self._key_lock(key):
            self.staged(key).unlink(missing_ok=True)
            for held in self._client_staged(key):
                with contextlib.suppress(OSError):
                    held.unlink()

    def wait(self, timeout: float) -> bool:
        """Block until something is pending. The ``threading.Event`` seam spec 15's fixture uses."""
        return self._signal.wait(timeout)

    @property
    def pending_keys(self) -> int:
        """How many repos have an unconsumed spool. Repos, never events (the wire says so too)."""
        return len(self.keys())
