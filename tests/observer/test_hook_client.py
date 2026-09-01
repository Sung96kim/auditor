"""The `hook` verb: Stage 0, the Stop path set, the drops, the posts and the spool (spec 13.1)."""

import io
import json
import threading
import time
from collections.abc import Iterator
from fnmatch import fnmatch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import auditr_observer
from auditor import discovery
from auditor.discovery import FileDiscovery, find_root, git_status_paths, parse_status_z
from auditor.graph.refine.models import ClientKind
from auditor.observer.events import CLIENT_SPOOL_GLOB, MAX_EVENT_PATHS
from auditor.paths import repo_dir_from_key, repo_dir_key


def _spooled(home: Path, key: str) -> list[Path]:
    """Every batch the client left behind for one repo, whatever it named the files."""
    return sorted((home / "repos" / key).glob(CLIENT_SPOOL_GLOB))


_SESSION = {"session_id": "s1", "cwd": ""}


def _payload(**over: object) -> dict:
    return {**_SESSION, **over}


class _Stranger(BaseHTTPRequestHandler):
    """Whatever else ended up on the port `daemon.json` still names: an HTTP 404, in JSON."""

    def do_POST(self) -> None:
        body = json.dumps({"error": "no route for POST /events"}).encode()
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Keep the stub off stderr; a hook that talks to it is not a test failure."""


@pytest.fixture
def recycled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An `AUDITOR_HOME` whose `daemon.json` names a port somebody else's server answers on.

    The port rule is a hash over 500 slots and the record outlives the daemon that wrote it, so
    this is the shape a client really meets, not a hypothetical one.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Stranger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / "recycled-home"
    (home / "observer").mkdir(parents=True)
    (home / "observer" / "daemon.json").write_text(
        json.dumps({"port": server.server_port})
    )
    monkeypatch.setenv("AUDITOR_HOME", str(home))
    try:
        yield home
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, daemon_server) -> Path:
    """An `AUDITOR_HOME` whose `daemon.json` names the live test server's port."""
    server, _ = daemon_server
    home = tmp_path / "client-home"
    (home / "observer").mkdir(parents=True)
    (home / "observer" / "daemon.json").write_text(json.dumps({"port": server.port}))
    monkeypatch.setenv("AUDITOR_HOME", str(home))
    return home


def test_the_stage_zero_sets_are_the_discovery_ones(tmp_path: Path):
    """A hook-side copy of the allowlist is only honest while it is a superset of the real one.

    Subset and not equality: `FileDiscovery.suffixes` is built from the process-global `REGISTRY`,
    which is 23 entries with the `ts` extra installed and 15 without, and P4's invariant is the
    subset. `_EXCLUDE_DIRS` is a module constant on both sides, so that one is an equality.
    """
    finder = FileDiscovery(tmp_path)
    assert set(finder.suffixes) <= auditr_observer._SUFFIXES
    assert set(finder.filenames) <= set(auditr_observer._FILENAMES)
    assert discovery._EXCLUDE_DIRS == auditr_observer._EXCLUDE_DIRS


def test_the_stop_path_argv_is_the_discovery_one():
    """Dropping `--untracked-files=all` from either copy hides every file in an untracked
    directory from the Stop path set, and the two functions' output over a one-file fixture
    cannot see the difference."""
    assert auditr_observer._STATUS_ARGS == discovery._STATUS_ARGS


def test_the_client_truncates_at_the_wire_cap_itself():
    """A client capped above the wire's own limit posts a body the daemon refuses whole."""
    assert auditr_observer._MAX_PATHS == MAX_EVENT_PATHS


def test_the_spool_the_client_writes_is_the_one_the_daemon_adopts():
    """Two spellings of one filename: the client names the file, the daemon globs for it."""
    assert fnmatch(auditr_observer.spool_name("deadbeef"), CLIENT_SPOOL_GLOB)


def test_the_client_and_the_daemon_name_the_same_spool_directory(git_repo: Path):
    """The filename is only half of it: another directory orphans every batch, silently.

    `repo_dir_from_key` owns the `repos/<key>` layout package-side, and the client re-spells both
    halves of the path in stdlib - the home and the layout. The glob pin above sees neither (L3).
    """
    key = repo_dir_key(git_repo)
    assert auditr_observer.home() / "repos" / key == repo_dir_from_key(key)


@pytest.mark.parametrize(
    "rel",
    ["node_modules/d.js", ".venv/lib/e.py", "build/out.js", "pkg/__pycache__/a.py"],
)
def test_stage_zero_drops_an_excluded_directory(rel: str):
    """The suffix half and the path half are two filters and only one of them was pinned.

    The parity test above guards the direction "never drop what discovery keeps", which is
    silent about a hook that stopped excluding anything: every excluded shape it parametrizes is
    dropped by discovery too, so its assertion never runs for them. This is the other direction,
    and a `/events` flooded with `node_modules` edits on every Stop is what it costs.
    """
    assert not auditr_observer.auditable_shape(rel)


@pytest.mark.parametrize(
    ("rel", "discovery_keeps", "hook_keeps"),
    [
        ("a.py", True, True),
        ("pkg/b.ts", True, True),
        ("package.json", True, True),
        (".env", True, True),
        ("cfg/.env.local", True, True),
        (
            "{root}/pkg/a.py",
            True,
            True,
        ),  # the absolute shape Claude Code actually sends
        ("pkg/c.md", False, False),
        ("node_modules/d.js", False, False),
        (".venv/lib/e.py", False, False),
        # the hook keeps these two and discovery's own globs drop them: a superset is the rule
        ("app/migrations/0001_initial.py", False, True),
        ("gen/x.gen.py", False, True),
    ],
)
def test_stage_zero_never_drops_what_discovery_keeps(
    tmp_path: Path, rel: str, discovery_keeps: bool, hook_keeps: bool
):
    """The hook is a subset filter, over the shape the client really sends.

    Both verdicts per id rather than one under an `if discovery keeps it` guard: under that
    guard the ids discovery drops asserted nothing at all, so a hook that had stopped filtering
    and a hook that had started dropping real edits read the same (L6).

    The root lives under a directory named `build` on purpose: `_EXCLUDE_DIRS` is a path-segment
    test, so an absolute path carries excluded names from outside the repo and every edit in such
    a checkout is dropped hook-side unless it is relativized first (C2). The daemon runs the real
    predicate again, so a hook that dropped more than this would lose an edit no one gets back.
    """
    root = tmp_path / "build" / "proj"
    root.mkdir(parents=True)
    named = Path(rel.format(root=root)) if "{root}" in rel else root / rel
    finder = FileDiscovery(root)
    assert finder.auditable_shape(named) is discovery_keeps
    hooked = auditr_observer.auditable_shape(
        auditr_observer._relative(str(named), root, root)
    )
    assert hooked is hook_keeps


@pytest.mark.parametrize(
    ("named", "cwd", "expected"),
    [
        ("{root}/pkg/a.py", "{root}", "pkg/a.py"),
        ("pkg/a.py", "{root}", "pkg/a.py"),
        # a session working in a subdirectory names `m.py` and means `src/m.py`; posting the
        # bare name stores a key the graph can never be looked up by (S9-16)
        ("m.py", "{root}/src", "src/m.py"),
        # `src/../x.py` is still in the repo; two levels up is not, and has no relative spelling
        ("../m.py", "{root}/src", "m.py"),
        ("../../outside.py", "{root}/src", ""),
        ("/etc/passwd", "{root}", ""),
    ],
)
def test_a_posted_path_is_repo_relative_or_is_not_posted(
    tmp_path: Path, named: str, cwd: str, expected: str
):
    """The daemon keys the graph on the repo-relative shape, so nothing else may reach it."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    assert (
        auditr_observer._relative(
            named.format(root=root), root, Path(cwd.format(root=root))
        )
        == expected
    )


@pytest.mark.parametrize(
    "payload",
    [
        "",
        " M a.py\0",
        "D  pkg/b.py\0",
        "R  pkg/d.py\0pkg/c.py\0",
        "C  pkg/f.py\0pkg/e.py\0",
        "?? new.py\0",
        " M a.py\0R  pkg/d.py\0pkg/c.py\0?? new.py\0",
        "XY\0",
    ],
)
def test_the_status_z_parser_matches_the_package_one(payload: str):
    assert auditr_observer.parse_status_z(payload) == parse_status_z(payload)


def test_the_stop_path_set_is_the_whole_dirty_tree(git_repo: Path):
    """Not a delta, and not the hook's own idea of one: the same paths `git_status_paths` names."""
    (git_repo / "a.txt").write_text("changed\n")
    (git_repo / "new.py").write_text("x = 1\n")
    assert auditr_observer.status_paths(git_repo) == git_status_paths(git_repo)


def test_the_key_matches_the_package_helper(git_repo: Path):
    """The key names the spool directory the daemon adopts; a second hash would name another."""
    assert auditr_observer.repo_dir_key(git_repo) == repo_dir_key(git_repo)


@pytest.mark.parametrize("marker", [".git", "pyproject.toml", ".auditor"])
def test_find_root_matches_the_package_helper(tmp_path: Path, marker: str):
    root = tmp_path / "proj"
    nested = root / "src" / "deep"
    nested.mkdir(parents=True)
    if marker == "pyproject.toml":
        (root / marker).write_text("")
    else:
        (root / marker).mkdir()
    assert auditr_observer.find_root(nested) == find_root(nested)


def test_an_edit_event_posts_the_path_it_named(
    git_repo: Path, tmp_path: Path, wired: Path
):
    """End to end over a real loopback daemon: the path reaches the daemon's own spool."""
    (git_repo / "m.py").write_text("x = 1\n")
    assert (
        auditr_observer._hook(
            "post-tool-use",
            "claude-code",
            _payload(
                cwd=str(git_repo), tool_input={"file_path": str(git_repo / "m.py")}
            ),
        )
        == 0
    )
    landed = tmp_path / "repos" / repo_dir_key(git_repo) / "spool.jsonl"
    # repo-relative, because that is the only shape `graph.facts` and the extractor's node ids
    # can be keyed on; an absolute path is a guaranteed miss (C1)
    assert json.loads(landed.read_text().strip())["paths"] == ["m.py"]
    # the client wrote its own copy before it posted; the 202 is what deletes it again
    assert _spooled(wired, repo_dir_key(git_repo)) == []


def test_a_subagent_edit_never_reaches_the_wire(
    git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """`agent_id` is present only inside a subagent, which spec 8.2 does not count as an edit.

    Asserted against what actually moves rather than against the client's own spool: the spool
    is written only when the daemon does not answer, and the `wired` fixture points at a live
    one, so the old shape of this test passed with the gate deleted.
    """
    posted: list[str] = []
    monkeypatch.setattr(
        auditr_observer, "_post", lambda path, body, timeout: posted.append(path)
    )
    auditr_observer._hook(
        "post-tool-use",
        "claude-code",
        _payload(
            cwd=str(git_repo),
            agent_id="a1",
            tool_input={"file_path": str(git_repo / "m.py")},
        ),
    )
    assert posted == []


@pytest.mark.parametrize("event", ["session-start", "stop", "session-end"])
def test_a_lifecycle_event_is_not_gated_by_the_subagent_rule(
    event: str, git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """Spec 8.2 drops a subagent's *tool call*, so the gate lives inside that branch alone.

    Ahead of the dispatch it also silenced attach, heartbeat and detach for any client that ever
    put an `agent_id` on a lifecycle payload, which is a session the daemon never learns about.
    """
    posted: list[str] = []
    monkeypatch.setattr(auditr_observer, "_run", lambda command: {})
    monkeypatch.setattr(
        auditr_observer,
        "_post",
        lambda path, body, timeout: posted.append(path) or (202, {"ok": True}),
    )
    auditr_observer._hook(
        event, "claude-code", _payload(cwd=str(git_repo), agent_id="a1")
    )
    assert posted


def test_a_non_auditable_edit_is_dropped_before_the_round_trip(
    git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    posted: list[str] = []
    monkeypatch.setattr(
        auditr_observer, "_post", lambda path, body, timeout: posted.append(path)
    )
    auditr_observer._hook(
        "post-tool-use",
        "claude-code",
        _payload(
            cwd=str(git_repo), tool_input={"file_path": str(git_repo / "notes.md")}
        ),
    )
    assert posted == []


def test_a_stop_event_sends_a_heartbeat_and_the_whole_dirty_tree(
    git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    (git_repo / "new.py").write_text("x = 1\n")
    (git_repo / "notes.md").write_text("hi\n")
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        auditr_observer,
        "_post",
        # `ok: True` is `SessionAck` for a session the daemon knows; `ok: False` is what makes
        # the branch re-attach, and the test below owns that case
        lambda path, body, timeout: sent.append((path, body)) or (202, {"ok": True}),
    )
    auditr_observer._hook("stop", "claude-code", _payload(cwd=str(git_repo)))
    assert [path for path, _ in sent] == ["/sessions/heartbeat", "/events"]
    body = sent[1][1]
    assert body["kind"] == "stop"
    assert body["paths"] == ["new.py"]  # notes.md is not a language auditor reads
    assert body["client"] == "claude-code"


def test_a_stop_batch_larger_than_the_wire_cap_is_truncated_not_refused(
    git_repo: Path, tmp_path: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """`EventRequest.paths` caps at 2,000 and P12 drops a refused body with no spool, so a big
    dirty tree would lose the whole Stop set rather than its tail.

    Read from whichever spool took it: the daemon's when it answered inside the 200 ms budget, the
    client's when Stage 0 over 2,000 paths outran it, which is drift D6 and is measured at 207 ms
    on some machines. An untruncated body is a 400, and a 400 is dropped rather than spooled, so
    neither file would exist and this would still fail.
    """
    monkeypatch.setattr(
        auditr_observer,
        "status_paths",
        lambda root: tuple(f"pkg/f{n}.py" for n in range(2500)),
    )
    assert (
        auditr_observer._hook("stop", "claude-code", _payload(cwd=str(git_repo))) == 0
    )
    key = repo_dir_key(git_repo)
    landed = [
        spool
        for spool in (
            tmp_path / "repos" / key / "spool.jsonl",
            *_spooled(wired, key),
        )
        if spool.exists()
    ]
    assert landed, "a truncated batch reaches one spool; a refused one reaches neither"
    posted = json.loads(landed[0].read_text().strip())["paths"]
    assert len(posted) == auditr_observer._MAX_PATHS


def test_a_stop_batch_at_the_cap_reaches_the_daemon_itself(
    git_repo: Path, tmp_path: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """The widened test above reads whichever spool took the batch, which is satisfied by the
    client's own; this is the half that says the daemon accepted a body at exactly the cap."""
    monkeypatch.setattr(
        auditr_observer,
        "status_paths",
        lambda root: tuple(f"pkg/f{n}.py" for n in range(auditr_observer._MAX_PATHS)),
    )
    auditr_observer._hook("stop", "claude-code", _payload(cwd=str(git_repo)))
    key = repo_dir_key(git_repo)
    landed = tmp_path / "repos" / key / "spool.jsonl"
    assert len(json.loads(landed.read_text().strip())["paths"]) == (
        auditr_observer._MAX_PATHS
    )
    assert _spooled(wired, key) == []  # a 202 deletes the client's copy


def test_a_stop_reattaches_a_session_the_daemon_does_not_know(
    git_repo: Path, wired: Path, daemon_router
):
    """A cold `ensure` can outrun session-start's 3 s budget, so the heartbeat is where a session
    the daemon never learned about gets repaired (P30)."""
    auditr_observer._hook("stop", "claude-code", _payload(cwd=str(git_repo)))
    assert [s.session_id for s in daemon_router.deps.sessions.live(now=0.0)] == ["s1"]


def test_the_repair_attach_gets_the_budget_the_attach_handler_needs(
    git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """`sessions_attach` runs the whole gate, `load_config` and a `git rev-parse`, and on the
    first attach of a daemon's life it opens the page too. The repair path calls that same
    handler at exactly the moment the daemon is coldest, so it may not be budgeted as an edit."""
    budgets: dict[str, float] = {}
    monkeypatch.setattr(
        auditr_observer,
        "_post",
        lambda path, body, timeout: (
            budgets.setdefault(path, timeout) and None or (202, {"ok": False})
        ),
    )
    auditr_observer._hook("stop", "claude-code", _payload(cwd=str(git_repo)))
    assert budgets["/sessions/attach"] == auditr_observer._REPAIR_TIMEOUT
    assert budgets["/sessions/attach"] > budgets["/sessions/heartbeat"]


def test_the_stop_batch_is_posted_on_its_own_budget(
    git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """A full `_MAX_PATHS` batch runs Stage 0 once per path on the daemon's request thread, so
    the per-edit 200 ms cannot deliver it and every large Stop batch would spool instead."""
    budgets: list[float] = []
    (git_repo / "new.py").write_text("x = 1\n")
    monkeypatch.setattr(
        auditr_observer,
        "_post",
        lambda path, body, timeout: budgets.append(timeout) or (202, {"ok": True}),
    )
    auditr_observer._hook("stop", "claude-code", _payload(cwd=str(git_repo)))
    assert budgets[-1] == auditr_observer._STOP_POST_TIMEOUT
    assert auditr_observer._STOP_POST_TIMEOUT > auditr_observer._POST_TIMEOUT


def test_an_unanswered_post_lands_in_the_spool_with_its_breadcrumb(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`Daemon.reconcile` reads `root.json` to give an adopted spool a loop, so both are written."""
    home = tmp_path / "no-daemon-home"
    monkeypatch.setenv("AUDITOR_HOME", str(home))
    (git_repo / "m.py").write_text("x = 1\n")
    auditr_observer._hook(
        "post-tool-use",
        "claude-code",
        _payload(cwd=str(git_repo), tool_input={"file_path": str(git_repo / "m.py")}),
    )
    key = repo_dir_key(git_repo)
    written = _spooled(home, key)
    assert len(written) == 1
    spooled = json.loads(written[0].read_text().strip())
    assert spooled["paths"] == ["m.py"]
    assert spooled["kind"] == "edit"
    assert spooled["at"] > 0
    assert spooled["batch"]  # what keeps a redelivery from being assessed twice
    crumb = json.loads((home / "repos" / key / "root.json").read_text())
    assert crumb["root"] == str(git_repo.resolve())


def test_a_refused_post_is_dropped_rather_than_spooled(
    git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """A 400 is the daemon rejecting this body; spooling it would replay the rejection for ever."""
    monkeypatch.setattr(auditr_observer, "repo_dir_key", lambda root: "not-a-key")
    (git_repo / "m.py").write_text("x = 1\n")
    auditr_observer._hook(
        "post-tool-use",
        "claude-code",
        _payload(cwd=str(git_repo), tool_input={"file_path": str(git_repo / "m.py")}),
    )
    assert _spooled(wired, "not-a-key") == []


@pytest.mark.parametrize(
    ("status", "left"),
    [
        (202, 0),  # the daemon took it
        (400, 0),  # its models will not validate this body, and no retry changes that
        (403, 0),  # `server.py` refuses a cross-origin or non-loopback request outright
        (413, 0),  # this body is over the wire's own 1 MiB cap
        (
            404,
            1,
        ),  # no route: a stranger on a recycled port, or a daemon without `/events`
        (405, 1),
        (
            411,
            1,
        ),  # this client always sends a Content-Length, so this came from somebody else
        (429, 1),
        (500, 1),  # `server.py` turns an unhandled handler exception into a JSON 500
        (503, 1),
    ],
)
def test_only_this_daemons_own_refusal_destroys_the_clients_copy(
    status: int,
    left: int,
    git_repo: Path,
    wired: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """`< 500` read every 4xx as authoritative, and most of them are not this daemon's at all.

    A 5xx is the daemon failing rather than refusing, so the batch is still good and comes back.
    A 404 is worse than that: it is the answer of a server that never saw an `/events` route, so
    unlinking on it destroys a durable batch nobody ever took (M1).
    """
    monkeypatch.setattr(
        auditr_observer, "_post", lambda path, body, timeout: (status, {})
    )
    (git_repo / "m.py").write_text("x = 1\n")
    auditr_observer._hook(
        "post-tool-use",
        "claude-code",
        _payload(cwd=str(git_repo), tool_input={"file_path": str(git_repo / "m.py")}),
    )
    assert len(_spooled(wired, repo_dir_key(git_repo))) == left


def test_a_stranger_on_a_recycled_port_does_not_get_to_delete_the_batch(
    git_repo: Path, recycled: Path
):
    """End to end against a real HTTP server that is not a daemon, over a real socket.

    `daemon.json` outlives the daemon that wrote it and the port rule is a hash over 500 slots,
    so the client does meet other people's servers. This one answers a well-formed JSON 404,
    which `_send` returns as a status like any other, and the batch has to survive it.
    """
    (git_repo / "m.py").write_text("x = 1\n")
    auditr_observer._hook(
        "post-tool-use",
        "claude-code",
        _payload(cwd=str(git_repo), tool_input={"file_path": str(git_repo / "m.py")}),
    )
    assert len(_spooled(recycled, repo_dir_key(git_repo))) == 1


def test_a_batch_is_durable_before_it_is_posted(
    git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """The hook's parent kills it on a timeout, and only a batch already on disk survives that.

    `_post` standing in for the kill is the honest shape: everything after the POST call is what
    an external SIGKILL takes away, so a spool written after it is a spool that never happens.
    """
    key = repo_dir_key(git_repo)

    def killed(path: str, body: dict, timeout: float) -> None:
        assert len(_spooled(wired, key)) == 1, (
            "the batch has to be durable before the wire"
        )
        raise SystemExit(9)

    monkeypatch.setattr(auditr_observer, "_post", killed)
    (git_repo / "m.py").write_text("x = 1\n")
    with pytest.raises(SystemExit):
        auditr_observer._hook(
            "post-tool-use",
            "claude-code",
            _payload(
                cwd=str(git_repo), tool_input={"file_path": str(git_repo / "m.py")}
            ),
        )
    assert len(_spooled(wired, key)) == 1


def test_a_kill_after_the_git_calls_and_before_the_wire_still_leaves_the_batch(
    git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """Spooling before the POST moved the window that loses a batch upstream, onto git.

    A slow `git` stands in for a large repo: what is asserted is that every git call has already
    finished and the batch is on disk by the time the wire is touched, so the parent's kill lands
    on a durable batch wherever in the chain it falls. The budgets those calls run on are what
    keeps the whole window inside the parent's own deadline, so they are asserted here too (M2).
    """
    key = repo_dir_key(git_repo)
    real_git = auditr_observer._git
    budgets: list[float] = []

    def slow(root: Path, *args: str, timeout: float) -> str | None:
        budgets.append(timeout)
        time.sleep(0.05)
        return real_git(root, *args, timeout=timeout)

    def killed(path: str, body: dict, timeout: float) -> None:
        assert len(_spooled(wired, key)) == 1, (
            "the batch has to be durable before the wire"
        )
        raise SystemExit(9)

    monkeypatch.setattr(auditr_observer, "_git", slow)
    monkeypatch.setattr(auditr_observer, "_post", killed)
    (git_repo / "m.py").write_text("x = 1\n")
    with pytest.raises(SystemExit):
        auditr_observer._hook(
            "post-tool-use",
            "claude-code",
            _payload(
                cwd=str(git_repo), tool_input={"file_path": str(git_repo / "m.py")}
            ),
        )
    assert set(budgets) == {auditr_observer._IDENTITY_TIMEOUT}
    assert len(_spooled(wired, key)) == 1


def test_one_batch_resolves_the_repo_identity_once(
    git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """Two git subprocesses on the tightest budget the client has, paid for twice per edit.

    `repo_dir_key` resolved it to name the spool directory and `_spool` resolved it again for
    the `root.json` crumb, inside the 200 ms an edit event gets (L4).
    """
    resolved: list[Path] = []
    real = auditr_observer.repo_identity
    monkeypatch.setattr(
        auditr_observer,
        "repo_identity",
        lambda root: resolved.append(root) or real(root),
    )
    (git_repo / "m.py").write_text("x = 1\n")
    auditr_observer._hook(
        "post-tool-use",
        "claude-code",
        _payload(cwd=str(git_repo), tool_input={"file_path": str(git_repo / "m.py")}),
    )
    assert resolved == [auditr_observer.find_root(git_repo)]
    crumb = json.loads(
        (wired / "repos" / repo_dir_key(git_repo) / "root.json").read_text()
    )
    assert crumb["identity"] == real(git_repo)


def test_an_edit_in_a_nested_checkout_is_attributed_to_the_session_repo(
    git_repo: Path, wired: Path, monkeypatch: pytest.MonkeyPatch
):
    """Review L9's deferral, pinned rather than only written down in the reference.

    `auditr scan` attributes the same file to the outer repo too, and re-rooting per edit would
    key a vendored checkout the user never configured - where spec 8.2's gate now refuses the
    adopted spool - so the edit would vanish rather than move. Changing that is a spec decision
    and not a patch, so what the client does today is asserted here instead (L7).
    """
    nested = git_repo / "vendor" / "sub"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    (nested / "n.py").write_text("x = 1\n")
    posted: list[dict] = []
    monkeypatch.setattr(
        auditr_observer,
        "_post",
        lambda path, body, timeout: posted.append(body) or (202, {}),
    )
    auditr_observer._hook(
        "post-tool-use",
        "claude-code",
        _payload(cwd=str(git_repo), tool_input={"file_path": str(nested / "n.py")}),
    )
    assert posted[0]["repo"] == str(auditr_observer.find_root(git_repo))
    assert posted[0]["paths"] == ["vendor/sub/n.py"]
    assert posted[0]["key"] == repo_dir_key(git_repo)


def test_the_spool_stops_growing_once_a_daemon_has_been_gone_long_enough(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """No daemon has ever run, so every edit spools; nothing else truncates the file."""
    home = tmp_path / "no-daemon-home"
    monkeypatch.setenv("AUDITOR_HOME", str(home))
    (git_repo / "m.py").write_text("x = 1\n")
    for _ in range(auditr_observer._MAX_SPOOL_BATCHES + 5):
        auditr_observer._hook(
            "post-tool-use",
            "claude-code",
            _payload(
                cwd=str(git_repo), tool_input={"file_path": str(git_repo / "m.py")}
            ),
        )
    assert (
        len(_spooled(home, repo_dir_key(git_repo)))
        == auditr_observer._MAX_SPOOL_BATCHES
    )


def test_session_start_attaches_and_session_end_detaches(
    git_repo: Path, wired: Path, daemon_router, monkeypatch: pytest.MonkeyPatch
):
    ran: list[str] = []
    monkeypatch.setattr(
        auditr_observer, "_run", lambda command: ran.append(command) or {}
    )
    auditr_observer._hook("session-start", "claude-code", _payload(cwd=str(git_repo)))
    # the cold launch is why SessionStart gets a 3 s budget at all; nothing else starts a daemon
    assert ran == ["ensure"]
    assert [s.session_id for s in daemon_router.deps.sessions.live(now=0.0)] == ["s1"]
    auditr_observer._hook("session-end", "claude-code", _payload(cwd=str(git_repo)))
    assert daemon_router.deps.sessions.live(now=0.0) == ()


def test_the_client_flag_defaults_to_the_wires_own_spelling():
    """`ClientKind` admits `claude-code`; the old `claude` default answered 400 on every post."""
    parsed = auditr_observer.build_parser().parse_args(["hook", "stop"])
    assert parsed.client == "claude-code"
    assert set(auditr_observer._CLIENTS) == {"claude-code", "codex"}


def _codex_payload(**over: object) -> dict[str, object]:
    """A Codex `Stop` payload: no `tool_input`, no `agent_id`, and a `turn_id` we do not read."""
    body: dict[str, object] = {
        "hook_event_name": "Stop",
        "session_id": "s1",
        "turn_id": "t1",
        "cwd": "/repo",
        "transcript_path": "/tmp/rollout.jsonl",
        "model": "gpt-5.1-codex",
        "permission_mode": "never",
        "stop_hook_active": False,
        "last_assistant_message": "done",
    }
    body.update(over)
    return body


def test_the_codex_reader_takes_the_two_fields_that_payload_carries():
    read = auditr_observer._codex_event(_codex_payload(cwd="/repo"))
    assert (read.cwd, read.session_id) == ("/repo", "s1")
    assert (read.agent_id, read.path) == ("", "")


def test_a_codex_payload_of_the_wrong_shape_still_reads():
    """Every field is read as a string or as "", so a number never reaches `Path()`."""
    read = auditr_observer._codex_event({"cwd": 7, "session_id": None})
    assert (read.cwd, read.session_id) == ("", "")


def test_a_codex_session_start_attaches_the_session_under_its_own_client(
    git_repo: Path, wired: Path, daemon_router, monkeypatch: pytest.MonkeyPatch
):
    """`ClientKind.CODEX` exists and, until this reader, was never written by anything."""
    ran: list[str] = []
    monkeypatch.setattr(auditr_observer, "_run", lambda verb: ran.append(verb))
    auditr_observer._hook("session-start", "codex", _codex_payload(cwd=str(git_repo)))
    assert ran == ["ensure"]
    attached = daemon_router.deps.sessions.live(now=0.0)
    assert [(s.session_id, s.client) for s in attached] == [("s1", ClientKind.CODEX)]


def test_a_codex_stop_posts_the_whole_git_status_path_set(
    git_repo: Path, tmp_path: Path, wired: Path
):
    """Codex has no per-edit hook, so Stop is the only edit path it has (spec 19.3)."""
    (git_repo / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    auditr_observer._hook("stop", "codex", _codex_payload(cwd=str(git_repo)))
    landed = tmp_path / "repos" / repo_dir_key(git_repo) / "spool.jsonl"
    body = json.loads(landed.read_text().strip())
    assert body["client"] == "codex"
    assert "new_module.py" in body["paths"]


def test_a_codex_post_tool_use_posts_nothing_because_there_is_no_path(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """The only `tool_name` Codex dispatches is `Bash`; this branch is unreachable for it."""
    posted: list[str] = []
    monkeypatch.setattr(
        auditr_observer, "_post", lambda path, body, timeout: posted.append(path)
    )
    assert auditr_observer._hook("post-tool-use", "codex", _codex_payload()) == 0
    assert posted == []


@pytest.mark.parametrize(
    "event", ["session-start", "post-tool-use", "stop", "session-end"]
)
def test_every_codex_event_exits_zero_whatever_happens(
    event: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """Codex runs the hook command straight from `hooks.json` with no wrapper script to swallow
    a non-zero exit, so this is a contract rather than an accident."""

    def boom(*args: object) -> int:
        raise RuntimeError("the graph observer is not the session's problem")

    monkeypatch.setattr(auditr_observer, "_hook", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert auditr_observer.main(["hook", event, "--client", "codex"]) == 0


@pytest.mark.parametrize(
    "event", ["session-start", "post-tool-use", "stop", "session-end"]
)
def test_the_kill_switch_reaches_every_hook_event(
    event: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """`disabled()` is read before the hook branch, so `AUDITOR_OBSERVER=0` posts nothing."""
    monkeypatch.setenv("AUDITOR_OBSERVER", "0")
    monkeypatch.setattr(
        auditr_observer,
        "_hook",
        lambda *a: pytest.fail("the kill switch let one through"),
    )
    assert auditr_observer.main(["hook", event]) == 0
    assert "disabled by AUDITOR_OBSERVER=0" in capsys.readouterr().err


@pytest.mark.parametrize(
    "event", ["session-start", "post-tool-use", "stop", "session-end"]
)
def test_every_hook_event_exits_zero_with_no_daemon_and_no_stdin(
    event: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The whole point of the file: a hook can never fail a session.

    `_run` is stubbed because the unstubbed `session-start` case really launches `auditr observer
    start` against a directory pytest then deletes, and leaves the daemon running (H1).
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "empty"))
    monkeypatch.setattr(auditr_observer, "_run", lambda command: {})
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.chdir(tmp_path)
    assert auditr_observer.main(["hook", event]) == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"cwd": 1234, "session_id": "s", "tool_input": {"file_path": "x.py"}},
        {"cwd": ["/tmp"], "session_id": "s"},
        {"cwd": ".", "session_id": {"a": 1}},
        {"cwd": ".", "session_id": "s", "agent_id": 7},
        {"cwd": ".", "session_id": "s", "tool_input": {"file_path": 5}},
        {"cwd": None, "session_id": None, "agent_id": None, "tool_input": None},
    ],
)
@pytest.mark.parametrize(
    "event", ["session-start", "post-tool-use", "stop", "session-end"]
)
def test_a_hostile_payload_shape_still_exits_zero(
    event: str,
    payload: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """The file's one absolute contract, against a well-formed JSON object of the wrong types.

    The empty-stdin case cannot reach this: every field was `payload.get(...) or ""`, so a `cwd`
    that was a number reached `Path()` and took the exit code with it. A non-string `session_id`
    is the quieter half - it rides onto the wire, `EventRequest` answers 400, and the batch is
    dropped with nothing said anywhere.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "empty"))
    monkeypatch.setattr(auditr_observer, "_run", lambda command: {})
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.chdir(tmp_path)
    assert auditr_observer.main(["hook", event]) == 0
    assert auditr_observer._FAILED not in capsys.readouterr().err


@pytest.mark.parametrize(
    "event", ["session-start", "post-tool-use", "stop", "session-end"]
)
def test_an_unexpected_failure_inside_the_hook_still_exits_zero(
    event: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """ "Nothing exits non-zero" is asserted in four places and was enforced in none of them.

    `main` guarded only `parse_args`, so anything `_hook` raised - a `RuntimeError` out of
    `Path.expanduser` for an `AUDITOR_HOME` naming a user that does not exist, say - left the
    process with a traceback and a 1. The session survives today only because the plugin script
    ignores the child's return code, which S12's Codex client will not.
    """

    def boom(*args: object) -> int:
        raise RuntimeError("the graph observer is not the session's problem")

    monkeypatch.setattr(auditr_observer, "_hook", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert auditr_observer.main(["hook", event]) == 0
    assert auditr_observer._FAILED in capsys.readouterr().err


def test_a_hand_run_verb_does_not_block_on_an_interactive_stdin(
    monkeypatch: pytest.MonkeyPatch,
):
    """The plugin always pipes; a user typing the verb into a shell would hang on `json.load`."""
    seen: list[dict] = []
    monkeypatch.setattr(
        auditr_observer,
        "_hook",
        lambda event, client, payload: seen.append(payload) or 0,
    )

    class _Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

        def read(self, *args: object) -> str:
            raise AssertionError("a terminal must not be read")

    monkeypatch.setattr("sys.stdin", _Terminal())
    assert auditr_observer.main(["hook", "stop"]) == 0
    assert seen == [{}]


def test_the_verb_reads_the_clients_own_json_off_stdin(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """The plugin script re-serializes the payload it parsed; this is the other end of that pipe."""
    seen: list[dict] = []
    monkeypatch.setattr(
        auditr_observer,
        "_hook",
        lambda event, client, payload: seen.append(payload) or 0,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(git_repo)})))
    assert auditr_observer.main(["hook", "stop", "--client", "claude-code"]) == 0
    assert seen == [{"cwd": str(git_repo)}]
