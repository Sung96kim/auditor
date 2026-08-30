"""The `daemon_server` fixture spec 15 asked for: an ephemeral port and an `http.client` caller."""

import http.client
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from auditor.graph.payloads import LogReport, RefinementsReport
from auditor.observer.events import EventQueue
from auditor.observer.payloads import (
    EvalsView,
    FlowView,
    GraphView,
    RefinementsView,
    ReposPayload,
    RunDetailView,
    RunsView,
)
from auditor.observer.routes import DaemonIdentity, Readers, Router, RouterDeps
from auditor.observer.server import ObserverServer
from auditor.observer.sessions import SessionBook
from auditor.user_settings import UserSettings


class FakeReaders(Readers):
    """Every store read as a canned value, so the transport tests need no database.

    The two counters are what lets a test prove a 304 skipped the page query and still paid for
    the tag (P14); `rows` is what the tag tracks, so a test can move it the way a run would.
    """

    def __init__(self) -> None:
        super().__init__(settings=UserSettings())
        self.tag_calls = 0
        self.page_calls = 0
        self.rows = 0
        self.detail: RunDetailView | None = None

    def runs(self, root: Path, *, identity: str | None = None) -> RunsView:
        self.page_calls += 1
        return RunsView(repo=str(root), identity="id", log=LogReport())

    def runs_tag(self, root: Path, *, identity: str | None = None) -> str:
        self.tag_calls += 1
        return f'W/"{root}-{self.rows}"'

    def repos(self) -> ReposPayload:
        return ReposPayload()

    def graph(self, root: Path, *, identity: str | None = None) -> GraphView:
        return GraphView(repo=str(root), identity="id")

    def refinements(
        self, root: Path, *, identity: str | None = None
    ) -> RefinementsView:
        return RefinementsView(
            repo=str(root), identity="id", refinements=RefinementsReport()
        )

    def evals(self, root: Path, *, identity: str | None = None) -> EvalsView:
        return EvalsView(repo=str(root), identity="id")

    def flow(self, root: Path, symbol: str, *, identity: str | None = None) -> FlowView:
        return FlowView(repo=str(root), identity="id", symbol=symbol)

    def run(
        self, root: Path, run_id: str, *, identity: str | None = None
    ) -> RunDetailView | None:
        return self.detail


class Caller:
    """One `http.client` connection against the server under test."""

    def __init__(self, port: int) -> None:
        self.port = port

    def request(
        self, method: str, path: str, body: Any = None, headers: dict | None = None
    ) -> tuple[int, dict, Any]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = None if body is None else json.dumps(body)
        conn.request(method, path, payload, headers or {})
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        try:
            return response.status, dict(response.getheaders()), json.loads(raw)
        except json.JSONDecodeError:
            return response.status, dict(response.getheaders()), raw.decode()


@pytest.fixture
def opened() -> list[str]:
    """Every URL the router asked a browser to open; spec 12.1 allows exactly one."""
    return []


@pytest.fixture
def readers() -> FakeReaders:
    """The fake the router reads through, exposed so a test can count its calls or make it raise."""
    return FakeReaders()


@pytest.fixture
def daemon_router(tmp_path: Path, opened: list[str], readers: FakeReaders) -> Router:
    """A router over scratch state, with the gate refusing nothing and a frozen start clock."""
    return Router(
        RouterDeps(
            identity=DaemonIdentity(
                home=tmp_path / "home",
                db_path=tmp_path / "home" / "index.db",
                version="0.10.5",
                compat=1,
            ),
            queue=EventQueue(lambda key: tmp_path / "repos" / key / "spool.jsonl"),
            sessions=SessionBook(expiry_minutes=45),
            readers=readers,
            page=lambda repo: "<!doctype html><html><body>observer</body></html>",
            gate=lambda request: "",
            open_page=opened.append,
        ),
        started_at=1_000.0,
    )


@pytest.fixture
def daemon_server(daemon_router: Router) -> Iterator[tuple[ObserverServer, Caller]]:
    """A live daemon on an ephemeral loopback port, torn down with the test."""
    server = ObserverServer(daemon_router.dispatch, port=0)
    daemon_router.url = server.url
    server.start()
    try:
        yield server, Caller(server.port)
    finally:
        server.stop()
