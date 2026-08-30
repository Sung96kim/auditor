"""Spec 8.2's sessions and the AND-gate that admits one."""

import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from auditor.graph.refine.models import ClientKind

MINUTE = 60.0


class AttachRequest(BaseModel):
    """``POST /sessions/attach``'s body, plus the home the caller resolved for itself."""

    model_config = ConfigDict(frozen=True)

    repo: str
    session_id: str
    cwd: str = ""
    client: ClientKind = ClientKind.CLAUDE_CODE
    #: None means the caller resolved no home at all, which is refused by name, never by cwd
    home: str | None = None


class SessionRef(BaseModel):
    """``POST /sessions/heartbeat`` and ``/sessions/detach``: one id and nothing else.

    Separate from :class:`AttachRequest`, whose `repo` is required, so a heartbeat body carrying
    only the id validates rather than answering 400.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str = ""


class Session(BaseModel):
    """One attached session and the clock its expiry is measured from."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    repo: str
    identity: str
    client: ClientKind = ClientKind.CLAUDE_CODE
    started_at: float = 0.0
    last_seen: float = 0.0


def attach_refusal(
    *,
    home: Path | None,
    daemon_home: Path,
    configured: bool,
    observer_allowed: bool,
    enabled: bool,
    worktrees: str,
    main_worktree: bool,
) -> str:
    """Why spec 8.2's gate refuses this repo, or "" when every clause passes.

    The home is read first: a mismatch means every other clause was answered from another
    install's settings. ``graph.enabled`` is deliberately not a clause (D2).
    """
    if home is None:
        return (
            "the caller named no home, so the daemon cannot prove it is the right one"
        )
    if home.resolve() != daemon_home.resolve():
        return f"the daemon's home is {daemon_home}, not {home}"
    if not configured:
        return "the repo is not configured for auditor"
    if not observer_allowed:
        return "the repo set observer_allowed = false"
    if not enabled:
        return "the observer is disabled in user settings"
    if worktrees != "all" and not main_worktree:
        return "this is a linked worktree and observer.worktrees is main"
    return ""


class SessionBook:
    """Attached sessions, with expiry computed on read so nothing has to tick (recon Q7).

    The daemon's own tick sweeps and reads on one thread while the HTTP handlers attach, beat and
    detach on others, so every method that touches the map takes the lock and the two readers
    iterate a snapshot rather than the live dict.
    """

    def __init__(self, *, expiry_minutes: float) -> None:
        self.expiry_seconds = expiry_minutes * MINUTE
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def attach(self, session: Session) -> Session:
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def expired(self, session: Session, *, now: float) -> bool:
        return now - session.last_seen > self.expiry_seconds

    def heartbeat(self, session_id: str, *, now: float) -> bool:
        """Move ``last_seen``; False for a session this daemon does not hold or has expired."""
        with self._lock:
            held = self._sessions.get(session_id)
            if held is None or self.expired(held, now=now):
                return False
            self._sessions[session_id] = held.model_copy(update={"last_seen": now})
        return True

    def detach(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def live(self, *, now: float) -> tuple[Session, ...]:
        """Every unexpired session, oldest first. Expiry is decided here, not by a timer."""
        with self._lock:
            held = tuple(self._sessions.values())
        return tuple(
            sorted(
                (s for s in held if not self.expired(s, now=now)),
                key=lambda s: s.started_at,
            )
        )

    def sweep(self, *, now: float) -> int:
        """Drop expired sessions and say how many went, for the idle tick."""
        with self._lock:
            gone = [
                k for k, s in list(self._sessions.items()) if self.expired(s, now=now)
            ]
            for key in gone:
                del self._sessions[key]
        return len(gone)
