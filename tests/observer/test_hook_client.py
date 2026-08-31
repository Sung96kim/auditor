"""The `hook` verb: Stage 0, the Stop path set, the drops, the posts and the spool (spec 13.1)."""

import io
import json
from pathlib import Path

import pytest

import auditr_observer
from auditor import discovery
from auditor.discovery import FileDiscovery, find_root, git_status_paths, parse_status_z
from auditor.paths import repo_dir_key

_SESSION = {"session_id": "s1", "cwd": ""}


def _payload(**over: object) -> dict:
    return {**_SESSION, **over}


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


@pytest.mark.parametrize(
    "rel",
    [
        "a.py",
        "pkg/b.ts",
        "pkg/c.md",
        "node_modules/d.js",
        ".venv/lib/e.py",
        "package.json",
        ".env",
        "cfg/.env.local",
        "app/migrations/0001_initial.py",
        "gen/x.gen.py",
        "{root}/pkg/a.py",  # the absolute shape Claude Code actually sends
    ],
)
def test_stage_zero_never_drops_what_discovery_keeps(tmp_path: Path, rel: str):
    """The hook is a subset filter, over the shape the client really sends.

    The root lives under a directory named `build` on purpose: `_EXCLUDE_DIRS` is a path-segment
    test, so an absolute path carries excluded names from outside the repo and every edit in such
    a checkout is dropped hook-side unless it is relativized first (C2). The daemon runs the real
    predicate again, so a hook that dropped more than this would lose an edit no one gets back.
    """
    root = tmp_path / "build" / "proj"
    root.mkdir(parents=True)
    named = Path(rel.format(root=root)) if "{root}" in rel else root / rel
    finder = FileDiscovery(root)
    if finder.auditable_shape(named):
        assert auditr_observer.auditable_shape(
            auditr_observer._relative(str(named), root)
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
    assert not (wired / "repos").exists()  # answered, so the client spooled nothing


def test_a_subagent_edit_never_reaches_the_wire(git_repo: Path, wired: Path):
    """`agent_id` is present only inside a subagent, which spec 8.2 does not count as an edit."""
    auditr_observer._hook(
        "post-tool-use",
        "claude-code",
        _payload(
            cwd=str(git_repo),
            agent_id="a1",
            tool_input={"file_path": str(git_repo / "m.py")},
        ),
    )
    assert not (wired / "repos").exists()


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
            wired / "repos" / key / "spool.jsonl",
        )
        if spool.exists()
    ]
    assert landed, "a truncated batch reaches one spool; a refused one reaches neither"
    posted = json.loads(landed[0].read_text().strip())["paths"]
    assert len(posted) == auditr_observer._MAX_PATHS


def test_a_stop_reattaches_a_session_the_daemon_does_not_know(
    git_repo: Path, wired: Path, daemon_router
):
    """A cold `ensure` can outrun session-start's 3 s budget, so the heartbeat is where a session
    the daemon never learned about gets repaired (P30)."""
    auditr_observer._hook("stop", "claude-code", _payload(cwd=str(git_repo)))
    assert [s.session_id for s in daemon_router.deps.sessions.live(now=0.0)] == ["s1"]


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
    spooled = json.loads((home / "repos" / key / "spool.jsonl").read_text().strip())
    assert spooled["paths"] == ["m.py"]
    assert spooled["kind"] == "edit"
    assert spooled["at"] > 0
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
    assert not (wired / "repos" / "not-a-key").exists()


def test_session_start_attaches_and_session_end_detaches(
    git_repo: Path, wired: Path, daemon_router, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(auditr_observer, "_run", lambda command: {})
    auditr_observer._hook("session-start", "claude-code", _payload(cwd=str(git_repo)))
    assert [s.session_id for s in daemon_router.deps.sessions.live(now=0.0)] == ["s1"]
    auditr_observer._hook("session-end", "claude-code", _payload(cwd=str(git_repo)))
    assert daemon_router.deps.sessions.live(now=0.0) == ()


def test_the_client_flag_defaults_to_the_wires_own_spelling():
    """`ClientKind` admits `claude-code`; the old `claude` default answered 400 on every post."""
    parsed = auditr_observer.build_parser().parse_args(["hook", "stop"])
    assert parsed.client == "claude-code"
    assert set(auditr_observer._CLIENTS) == {"claude-code", "codex"}


def test_a_codex_hook_is_accepted_and_does_nothing_yet(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    """S12 adds the reader; until then the verb parses and the event goes nowhere."""
    posted: list[str] = []
    monkeypatch.setattr(
        auditr_observer, "_post", lambda path, body, timeout: posted.append(path)
    )
    assert auditr_observer._hook("stop", "codex", _payload(cwd=str(git_repo))) == 0
    assert posted == []


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
