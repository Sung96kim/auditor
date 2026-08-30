"""Spec 8.2's AND gate and the sessions it admits, with expiry decided on read."""

from pathlib import Path

import pytest

from auditor.graph.refine.models import ClientKind
from auditor.observer.sessions import Session, SessionBook, attach_refusal

_GATE = {
    "home": Path("/h"),
    "daemon_home": Path("/h"),
    "configured": True,
    "observer_allowed": True,
    "enabled": True,
    "worktrees": "main",
    "main_worktree": True,
}


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({}, ""),
        ({"home": Path("/other")}, "the daemon's home is /h, not /other"),
        ({"configured": False}, "the repo is not configured for auditor"),
        ({"observer_allowed": False}, "the repo set observer_allowed = false"),
        ({"enabled": False}, "the observer is disabled in user settings"),
        (
            {"main_worktree": False},
            "this is a linked worktree and observer.worktrees is main",
        ),
        ({"main_worktree": False, "worktrees": "all"}, ""),
    ],
)
def test_the_attach_gate_names_the_first_clause_that_refuses(override, expected):
    """Spec 8.2's AND-gate. `graph.enabled` is not a clause and must never become one (D2)."""
    assert attach_refusal(**{**_GATE, **override}) == expected


def test_the_home_clause_is_read_before_any_settings_clause():
    """A home mismatch means every other clause was answered from another install's settings."""
    refusal = attach_refusal(
        **{**_GATE, "home": Path("/other"), "configured": False, "enabled": False}
    )
    assert refusal == "the daemon's home is /h, not /other"


def test_a_caller_that_resolved_no_home_is_refused_by_name():
    """`home` defaults to None, never to "", whose `Path("").resolve()` is the daemon's own cwd."""
    assert attach_refusal(**{**_GATE, "home": None}) == (
        "the caller named no home, so the daemon cannot prove it is the right one"
    )


def _session(**kw) -> Session:
    base = {
        "session_id": "s1",
        "repo": "/r",
        "identity": "/r/.git",
        "client": ClientKind.CLAUDE_CODE,
        "started_at": 0.0,
        "last_seen": 0.0,
    }
    return Session(**{**base, **kw})


def test_a_session_expires_on_read_with_nothing_ticking():
    """Spec 8.2's 45 minutes, decided by the clock the caller passes (recon Q7)."""
    book = SessionBook(expiry_minutes=45)
    book.attach(_session())
    assert book.live(now=45 * 60) != ()
    assert book.live(now=45 * 60 + 1) == ()


def test_a_heartbeat_moves_last_seen_and_an_unknown_one_is_refused():
    book = SessionBook(expiry_minutes=45)
    book.attach(_session())
    assert book.heartbeat("s1", now=100.0) is True
    assert book.live(now=45 * 60 + 1) != ()
    assert book.heartbeat("nobody", now=100.0) is False


def test_a_heartbeat_cannot_revive_an_expired_session():
    """An expired session is gone, so a late `Stop` must open a new one rather than resume it."""
    book = SessionBook(expiry_minutes=45)
    book.attach(_session())
    assert book.heartbeat("s1", now=45 * 60 + 1) is False


def test_detach_is_idempotent_and_the_sweep_counts_what_it_dropped():
    book = SessionBook(expiry_minutes=45)
    book.attach(_session())
    book.attach(_session(session_id="s2", started_at=1.0, last_seen=10_000.0))
    assert book.detach("s1") is True
    assert book.detach("s1") is False
    assert book.sweep(now=10_000.0) == 0
    assert book.sweep(now=10_000.0 + 45 * 60 + 1) == 1
    assert book.live(now=10_000.0 + 45 * 60 + 1) == ()
