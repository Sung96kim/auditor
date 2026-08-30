"""The events the hooks post: the spool is the truth and the in-memory set is the wakeup."""

from pathlib import Path

import pytest

from auditor.observer.events import (
    MAX_EVENT_PATHS,
    Event,
    EventKind,
    EventQueue,
    EventRequest,
    Spool,
)


@pytest.fixture
def queue(tmp_path: Path) -> EventQueue:
    """A queue whose spools live under ``tmp_path`` instead of the user's home."""
    return EventQueue(lambda key: tmp_path / "repos" / key / "spool.jsonl")


#: one legal `repo_dir_key`, which is the only shape `EventRequest.key` admits
_KEY = "a" * 40


def _event(**kw) -> Event:
    return Event(**{"repo": "/r", "paths": ("a.py",), "at": 100.0, **kw})


def test_an_accepted_event_is_on_disk_before_the_202_returns(queue, tmp_path):
    """The spool is the durability seam: a daemon that dies after the 202 loses nothing."""
    queue.put("k", _event())
    assert (tmp_path / "repos" / "k" / "spool.jsonl").exists()
    assert queue.keys() == ("k",)
    assert queue.accepted == 1


def test_the_drain_reads_the_spool_and_not_the_signal(queue, tmp_path):
    """S8c consumes the spool, so a restart between the 202 and the loop still runs the batch."""
    queue.put("k", _event())
    queue.put("k", _event(paths=("b.py",), kind=EventKind.STOP))
    fresh = EventQueue(lambda key: tmp_path / "repos" / key / "spool.jsonl")
    assert fresh.adopt(["k", "never-seen"]) == 1
    drained = fresh.drain("k")
    assert [e.paths for e in drained] == [("a.py",), ("b.py",)]
    assert [e.kind for e in drained] == [EventKind.EDIT, EventKind.STOP]


def test_a_put_during_a_drain_is_not_lost(queue, tmp_path, monkeypatch):
    """The event that already answered 202 must survive the drain that raced it (C-1)."""
    path = tmp_path / "repos" / "k" / "spool.jsonl"
    queue.put("k", _event())
    read = Spool.read

    def write_while_reading(self) -> tuple[Event, ...]:
        # another process's `put`: it lands under the live name while the drain reads the renamed
        Spool(path).append(_event(paths=("b.py",), kind=EventKind.STOP))
        return read(self)

    monkeypatch.setattr(Spool, "read", write_while_reading)
    drained = queue.drain("k")
    monkeypatch.undo()
    queue.consumed("k")
    assert [e.paths for e in drained] == [("a.py",)]
    assert queue.keys() == ("k",)
    assert path.exists()
    assert [e.paths for e in queue.drain("k")] == [("b.py",)]
    queue.consumed("k")
    assert queue.keys() == ()
    assert not path.exists()
    assert list(path.parent.glob("*.draining")) == []


def test_the_queue_counts_repos_and_says_so(queue):
    """`pending_keys` is repos, never events: two events for one repo are one pending key (M-3)."""
    queue.put("k", _event())
    queue.put("k", _event(paths=("b.py",)))
    assert queue.pending_keys == 1
    assert queue.accepted == 2


def test_a_path_set_over_the_cap_is_refused_by_the_model():
    """One `Stop` names a whole dirty tree; past the cap the body is a mistake, not a batch."""
    with pytest.raises(ValueError):
        EventRequest(
            repo="/r",
            key=_KEY,
            paths=tuple(f"f{i}.py" for i in range(MAX_EVENT_PATHS + 1)),
        )


def test_a_drained_spool_is_cleared_and_the_key_goes_with_it(queue):
    queue.put("k", _event())
    assert queue.drain("k") != ()
    assert queue.keys() == ()
    queue.consumed("k")
    assert queue.drain("k") == ()
    assert queue.wait(0.0) is False


def test_the_signal_is_set_for_a_consumer_that_waits(queue):
    """The `threading.Event` seam spec 15's `daemon_server` fixture blocks the loop through."""
    assert queue.wait(0.0) is False
    queue.put("k", _event())
    assert queue.wait(0.0) is True


def test_a_torn_spool_line_is_skipped_not_raised(tmp_path):
    """A half-written line from a killed daemon must not take the next start down with it."""
    path = tmp_path / "spool.jsonl"
    path.write_text('{"repo": "/r", "paths": ["a.py"]}\n{"repo": \n')
    assert [e.repo for e in Spool(path).read()] == ["/r"]


def test_a_missing_spool_reads_as_empty(tmp_path):
    assert Spool(tmp_path / "nothing.jsonl").read() == ()


def test_a_daemon_killed_mid_drain_still_hands_the_batch_to_its_successor(
    queue, tmp_path
):
    """`drain` unlinked the staged file before `consume` ran, so a kill there lost the batch."""
    for name in ("a", "b", "c"):
        queue.put("k", _event(paths=(f"{name}.py",)))
    drained = queue.drain("k")  # the process dies here, before any consumer sees these
    assert len(drained) == 3
    staged = tmp_path / "repos" / "k" / "spool.draining"
    assert staged.exists()
    fresh = EventQueue(lambda key: tmp_path / "repos" / key / "spool.jsonl")
    assert fresh.adopt(["k"]) == 1
    assert [e.paths for e in fresh.drain("k")] == [("a.py",), ("b.py",), ("c.py",)]


def test_a_staged_batch_is_drained_ahead_of_a_spool_written_after_it(queue, tmp_path):
    """The staged batch was accepted first, so it has to reach the consumer first."""
    queue.put("k", _event(paths=("older.py",)))
    assert [e.paths for e in queue.drain("k")] == [("older.py",)]
    queue.put("k", _event(paths=("newer.py",)))
    fresh = EventQueue(lambda key: tmp_path / "repos" / key / "spool.jsonl")
    assert fresh.adopt(["k"]) == 1
    assert [e.paths for e in fresh.drain("k")] == [("older.py",)]
    fresh.consumed("k")
    assert [e.paths for e in fresh.drain("k")] == [("newer.py",)]


def test_a_line_torn_mid_character_is_skipped_not_raised(tmp_path):
    """A repo path with a non-ASCII character makes this the ordinary kill, not a corner."""
    path = tmp_path / "spool.jsonl"
    path.write_bytes(b'{"repo": "/r", "paths": ["a.py"]}\n{"repo": "/caf\xc3')
    assert [e.repo for e in Spool(path).read()] == ["/r"]
