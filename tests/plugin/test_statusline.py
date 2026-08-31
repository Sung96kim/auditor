import ast
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _support import result_with

from auditor.discovery import find_root
from auditor.models import Severity
from auditor.observer.scheduling import LoopState
from auditor.paths import auditor_home, ensure_repo_dir, observer_dir, repo_dir_key
from auditor.status import merge_status, write_graph_status, write_status

SCRIPT = (
    Path(__file__).resolve().parents[2] / "plugin" / "statusline" / "auditor_status.py"
)


def _module():
    """Import the status line as a module, to compare its key with the package's."""
    spec = importlib.util.spec_from_file_location("auditor_status", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(cwd: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(cwd)}),
        capture_output=True,
        text=True,
    )
    return proc.stdout


def _write_status(cwd: Path, severity: dict, configured=True, age=0):
    """Write where a scan from ``cwd`` would, through production's own writer.

    The status line walks up to the root first, so the helper has to as well or a nested-cwd test
    writes to the wrong key. Hand-building the payload here made every test in this file agree
    with a literal instead of with `auditor.status`.
    """
    merge_status(
        find_root(cwd),
        "scan",
        {
            "severity": severity,
            "configured": configured,
            "written_at": int(time.time()) - age,
        },
    )


def _write_raw(cwd: Path, raw: str) -> None:
    (ensure_repo_dir(find_root(cwd)) / "status.json").write_text(raw)


def test_no_config_when_cache_absent(tmp_path):
    assert "not set up" in _run(tmp_path)


def test_clean_when_all_zero(tmp_path):
    _write_status(
        tmp_path, {"blocking": 0, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    assert "clean" in _run(tmp_path)


def test_spells_counts_and_rolls_lower(tmp_path):
    _write_status(
        tmp_path, {"blocking": 2, "high": 5, "medium": 4, "low": 3, "suggestion": 10}
    )
    out = _run(tmp_path)
    assert "2 blocking" in out and "5 high" in out and "+17 lower" in out


def test_stale_marker(tmp_path):
    _write_status(
        tmp_path,
        {"blocking": 1, "high": 0, "medium": 0, "low": 0, "suggestion": 0},
        age=3600,
    )
    assert "⟳" in _run(tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",  # decode error
        "[]",  # valid JSON, non-dict payload
        '{"graph": {"nodes": 3}}',  # only the other writer's block, and it carries no clock
    ],
)
def test_corrupt_cache_degrades_to_not_set_up(tmp_path, raw):
    _write_raw(tmp_path, raw)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr
    assert "not set up" in proc.stdout


@pytest.mark.parametrize(
    "raw",
    [
        '{"scan": {"severity": 5, "configured": true, "written_at": 0}}',
        '{"scan": {"severity": {"blocking": "x"}, "written_at": "soon"}}',
    ],
)
def test_malformed_fields_degrade_without_crashing(tmp_path, raw):
    _write_raw(tmp_path, raw)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr


def test_key_matches_the_package_helper(git_repo):
    """The status line has to land on the same directory `auditr scan` wrote."""
    assert _module()._repo_dir_key(git_repo) == repo_dir_key(git_repo)


@pytest.mark.parametrize("marker", [".git", "pyproject.toml", ".auditor"])
def test_find_root_matches_the_package_helper(tmp_path, marker):
    """The other duplicated half: walk up the same way, or the key is computed for a different
    root and the two land in different directories. Parametrized over every marker, since a
    git-only fixture cannot tell whether the other two are still in the list."""
    root = tmp_path / "proj"
    nested = root / "src" / "deep"
    nested.mkdir(parents=True)
    if marker == "pyproject.toml":
        (root / marker).write_text("")
    else:
        (root / marker).mkdir()
    assert _module()._find_root(nested) == find_root(nested)


@pytest.mark.parametrize("value", [None, "", "~/x", "/tmp/auditor-home-parity"])
def test_home_matches_the_package_helper(monkeypatch, value):
    """The third duplicated helper. An empty AUDITOR_HOME has to mean unset on both sides, or
    the package writes state into the working directory and the status line reads elsewhere."""
    if value is None:
        monkeypatch.delenv("AUDITOR_HOME", raising=False)
    else:
        monkeypatch.setenv("AUDITOR_HOME", value)
    assert _module()._home() == auditor_home()


def test_statusline_parses_as_python_39():
    """Syntax only: it catches what 3.9 cannot parse (a `match` statement, a parenthesized
    `with`), not a stdlib API a 3.9 interpreter lacks. `X | None` parses everywhere, which is
    why the module carries `from __future__ import annotations` instead."""
    assert isinstance(ast.parse(SCRIPT.read_text(), feature_version=(3, 9)), ast.Module)


def test_walks_up_to_the_repo_root(git_repo):
    _write_status(
        git_repo, {"blocking": 1, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    nested = git_repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert "1 blocking" in _run(nested)


def test_ignores_a_legacy_in_repo_status_file(tmp_path):
    legacy = tmp_path / ".auditor"
    legacy.mkdir()
    (legacy / ".status.json").write_text(
        json.dumps(
            {
                "severity": {"blocking": 9},
                "configured": True,
                "written_at": int(time.time()),
            }
        )
    )
    assert "not set up" in _run(tmp_path)


def test_statusline_reads_what_write_status_wrote(tmp_path):
    """The one test that closes the loop: production writes the file, the shipped status line
    reads it. It touches all three keys, so renaming any of them in `auditor.status` fails here
    instead of silently blanking or misreporting the segment in every session."""
    write_status(
        tmp_path,
        [result_with("m.py", Severity.HIGH, Severity.LOW)],
        configured=True,
    )
    out = _run(tmp_path)
    assert "1 high" in out and "+1 lower" in out  # severity
    assert "⟳" not in out  # written_at: a scan that just ran is not stale


def test_statusline_reads_the_unconfigured_flag_write_status_wrote(tmp_path):
    write_status(tmp_path, [], configured=False)
    assert "not set up" in _run(tmp_path)


def _publish_daemon() -> None:
    """What a live daemon leaves behind: the file the segment reads for liveness."""
    observer_dir().mkdir(parents=True, exist_ok=True)
    (observer_dir() / "daemon.json").write_text(json.dumps({"pid": 1, "port": 7490}))


def _write_graph(cwd: Path, **over) -> None:
    payload = {
        "nodes": 1234,
        "refined": 7,
        "state": "observing",
        "expiry_seconds": 2700,
    }
    payload.update(over)
    payload.setdefault("written_at", int(time.time()))
    merge_status(find_root(cwd), "graph", payload)


def test_the_graph_segment_renders_after_the_severity_one(tmp_path):
    """Spec 12.4's line, in spec 12.4's order."""
    _write_status(
        tmp_path, {"blocking": 1, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    _write_graph(tmp_path)
    _publish_daemon()
    out = _run(tmp_path)
    assert "1 blocking" in out
    assert "◆" in out and "graph 1.2k · 7 refined · observing" in out
    assert out.index("blocking") < out.index("graph")


@pytest.mark.parametrize(
    ("nodes", "shown"),
    [
        (0, "0"),
        (940, "940"),
        (1234, "1.2k"),
        (12_500, "12.5k"),
        (3_400_000, "3.4M"),
        # one decimal place rounds anything from 999,950 up to `1000.0k`, which is wider than
        # the `M` spelling it should already have crossed into
        (999_949, "999.9k"),
        (999_950, "1.0M"),
        (999_999, "1.0M"),
        # a count from a torn or hostile block is still a count, never a negative one
        (-5, "0"),
    ],
)
def test_the_node_count_is_compacted(tmp_path, nodes, shown):
    _write_status(
        tmp_path, {"blocking": 0, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    _write_graph(tmp_path, nodes=nodes)
    _publish_daemon()
    assert f"graph {shown} ·" in _run(tmp_path)


def test_a_paused_loop_is_shown_as_the_daemon_wrote_it(tmp_path):
    """`LoopState`'s own words; the statusline never invents a state name."""
    _write_status(
        tmp_path, {"blocking": 0, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    _write_graph(tmp_path, state="paused:budget")
    _publish_daemon()
    assert "· paused:budget" in _run(tmp_path)


def test_the_state_words_are_the_loops_own():
    """The statusline is stdlib and hand-copies the eight words it will echo; this is the pin."""
    assert {state.value for state in LoopState} == _module()._STATES


def test_a_state_the_loop_never_wrote_is_not_echoed(tmp_path):
    """`state` is JSON off disk and the segment is one line of a live terminal.

    A newline in it breaks the widget and an escape sequence repaints the terminal, so the word
    is rendered only when it is one the daemon could have written.
    """
    _write_status(
        tmp_path, {"blocking": 0, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    _write_graph(tmp_path, state="obs\x1b[31mX\nY")
    _publish_daemon()
    out = _run(tmp_path)
    assert "\n" not in out and "\x1b[31m" not in out
    assert "· observing" in out


def test_a_block_stamped_in_the_future_is_not_treated_as_live(tmp_path):
    """A clock-skewed writer, or a home on a network filesystem, would otherwise never expire."""
    _write_status(
        tmp_path, {"blocking": 0, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    _write_graph(tmp_path, written_at=int(time.time()) + 86_400)
    _publish_daemon()
    assert "graph off" in _run(tmp_path)


@pytest.mark.parametrize(
    ("field", "shown"), [("nodes", "graph 0 ·"), ("refined", "· 0 refined")]
)
def test_a_boolean_count_is_not_a_count_of_one(tmp_path, field: str, shown: str):
    """`True` is an `int` in Python, and `{"nodes": true}` would otherwise render `graph 1`."""
    _write_status(
        tmp_path, {"blocking": 0, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    _write_graph(tmp_path, **{field: True})
    _publish_daemon()
    assert _module()._num(True) == 0
    assert shown in _run(tmp_path)


def test_a_stale_graph_block_reads_off(tmp_path):
    """The block outlives the process that wrote it, so age is what makes `observing` a lie."""
    _write_status(
        tmp_path, {"blocking": 0, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    _write_graph(tmp_path, written_at=int(time.time()) - 2701)
    _publish_daemon()
    out = _run(tmp_path)
    assert "graph off" in out and "observing" not in out
    assert "clean" in out  # the severity segment is untouched


def test_a_fresh_block_with_no_daemon_reads_off(tmp_path):
    """A daemon that stopped a second ago leaves a fresh block and no `daemon.json`."""
    _write_status(
        tmp_path, {"blocking": 0, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    _write_graph(tmp_path)
    assert "graph off" in _run(tmp_path)


def test_a_daemon_with_no_block_yet_reads_off(tmp_path):
    _write_status(
        tmp_path, {"blocking": 0, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    _publish_daemon()
    assert "graph off" in _run(tmp_path)


def test_a_repo_no_observer_ever_watched_keeps_the_line_it_had(tmp_path):
    """No block and no daemon means no segment: an observer-free user sees no new noise."""
    _write_status(
        tmp_path, {"blocking": 2, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    out = _run(tmp_path)
    assert "2 blocking" in out
    assert "graph" not in out and "◆" not in out


@pytest.mark.parametrize(
    "block",
    [
        {"nodes": "many", "refined": None, "state": 7, "expiry_seconds": "soon"},
        {"nodes": 3},
        [],
        "graph",
    ],
)
def test_a_torn_graph_block_degrades_that_segment_alone(tmp_path, block):
    """Spec 12.4: a torn or missing file degrades that segment only."""
    _write_status(
        tmp_path, {"blocking": 1, "high": 0, "medium": 0, "low": 0, "suggestion": 0}
    )
    merge_status(find_root(tmp_path), "graph", block)
    _publish_daemon()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr
    assert "1 blocking" in proc.stdout


def test_statusline_reads_what_write_graph_status_wrote(tmp_path):
    """The loop closed on the observer's own writer, the way it is closed on `write_status`."""
    write_status(tmp_path, [], configured=True)
    write_graph_status(
        tmp_path, nodes=1234, refined=7, state="observing", expiry_seconds=2700
    )
    _publish_daemon()
    assert "graph 1.2k · 7 refined · observing" in _run(tmp_path)


def test_the_graph_block_does_not_disturb_the_scan_block(tmp_path):
    """Two writers, one file: `merge_status` is read-merge-replace on both sides."""
    write_status(tmp_path, [result_with("m.py", Severity.HIGH)], configured=True)
    write_graph_status(
        tmp_path, nodes=10, refined=0, state="building", expiry_seconds=2700
    )
    _publish_daemon()
    out = _run(tmp_path)
    assert "1 high" in out and "graph 10 · 0 refined · building" in out
