"""Spec 12.1's live page: the bootstrap that turns it on, and the page with no repo."""

import json
import re
import time
from pathlib import Path

import pytest

from auditor.graph.refine.models import MODEL_RUNNERS, RunnerKind
from auditor.graph.viz import empty_payload, render_app
from auditor.observer.daemon import IdleTimer, repo_page
from auditor.observer.payloads import GraphView
from auditor.observer.routes import Readers
from auditor.user_settings import UserSettings


class _OneRepo:
    """A `Readers` stand-in whose graph read is the only thing `repo_page` uses."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def graph(self, root: Path, *, identity: str | None = None) -> GraphView:
        self.asked.append(str(root))
        return GraphView(repo=str(root), identity="id", graph=empty_payload())


#: the injector's own spelling, so the 1.8 MB bundle's inlined script cannot be mistaken for it
_INJECTED = re.compile(r"<script>window\.(__AUDITOR_[A-Z]+__)=(.*?);</script>")


def _globals(html: str) -> dict:
    """Every global the injector appended inside the body, decoded, and no others.

    Every match rather than the last two: a third global would otherwise be dropped in silence,
    and a global injected past `</body>` reads here as an absence rather than as a decode crash.
    """
    tail = html.rsplit("</body>", 1)[0] if "</body>" in html else html
    return {
        found.group(1): json.loads(found.group(2)) for found in _INJECTED.finditer(tail)
    }


def test_graph_serve_injects_no_bootstrap_so_the_page_stays_static():
    """`graph serve` has no `/api/*` at all, so a page that polled it would 404 every 3 s.

    The injected tag is what is absent, not the name: `bootstrap.ts` reads the global, so the
    bundle spells it and a minifier cannot rename a property read off `window`.
    """
    html = render_app({"meta": {}, "nodes": [], "edges": [], "clusters": []})
    assert "<script>window.__AUDITOR_GRAPH__=" in html
    assert "<script>window.__AUDITOR_OBSERVER__=" not in html


def test_the_bootstrap_is_a_second_global_next_to_the_payload():
    """First paint, no probe and no doomed request: the flag is on the page before React runs."""
    html = render_app(
        {"meta": {}, "nodes": [], "edges": [], "clusters": []},
        bootstrap={"live": True, "base": "/", "repo": "/w"},
    )
    injected = _globals(html)
    assert injected["__AUDITOR_OBSERVER__"] == {"live": True, "base": "/", "repo": "/w"}
    # both are inside the body: `_globals` reads the tail before `</body>` and nothing after it
    assert set(injected) == {"__AUDITOR_GRAPH__", "__AUDITOR_OBSERVER__"}


@pytest.mark.parametrize(
    "hostile",
    ["</script><b>", "<!--<script>", "<!-- <script>", "<!--<script ", "<script>"],
    ids=["closer", "comment opener", "spaced", "unterminated", "opener"],
)
def test_a_payload_holding_a_tag_cannot_steer_the_parser(hostile: str):
    """The escape is per injected global, not per call site, so both blobs get it.

    `<!--<script` puts the tokenizer in script-data-double-escaped state, where the real
    `</script>` stops closing the element and the rest of the document becomes script text.
    """
    harmless = render_app(
        {"meta": {"repo": "ok"}, "nodes": [], "edges": [], "clusters": []},
        bootstrap={"live": True, "base": "/", "repo": "/w"},
    )
    drawn = render_app(
        {"meta": {"repo": hostile}, "nodes": [], "edges": [], "clusters": []},
        bootstrap={"live": True, "base": "/", "repo": hostile},
    )
    # not one `<` more than the same page carrying a harmless value: none of it reached the HTML
    assert drawn.count("<") == harmless.count("<")
    assert drawn.count("</script>") == harmless.count("</script>")
    assert drawn.count(hostile) == harmless.count(hostile)
    # escaped on the way out and decoded on the way back in: the value itself is not mangled
    injected = _globals(drawn)
    assert injected["__AUDITOR_OBSERVER__"]["repo"] == hostile
    assert injected["__AUDITOR_GRAPH__"]["meta"]["repo"] == hostile


def test_the_no_repo_page_carries_a_meta_the_app_can_read():
    """The daemon's own `open_browser` URL names no repo, and `data.meta.repo` was unguarded."""
    document = empty_payload()
    assert document["meta"]["theme"] == "dark"
    assert document["meta"]["node_cap"] is None
    assert document["nodes"] == [] and document["edges"] == []


def test_the_page_with_no_repo_reads_no_store_and_still_bootstraps():
    """Regression: the URL `sessions/attach` hands back is the bare page, which used to throw."""
    readers = _OneRepo()
    html = repo_page(readers)(None)
    assert readers.asked == []
    injected = _globals(html)
    assert injected["__AUDITOR_OBSERVER__"]["repo"] == ""
    assert injected["__AUDITOR_GRAPH__"]["meta"]["theme"] == "dark"


def test_the_page_with_a_repo_names_it_in_the_bootstrap(tmp_path):
    readers = _OneRepo()
    html = repo_page(readers)(str(tmp_path))
    assert readers.asked == [str(tmp_path)]
    assert _globals(html)["__AUDITOR_OBSERVER__"]["repo"] == str(tmp_path)


@pytest.mark.parametrize(
    "repo", ["/nope/never", "relative/path", "", "   ", "<!--<script>"]
)
def test_the_page_opens_no_store_for_a_repo_that_is_not_one(repo: str):
    """`/` took its `repo` raw, and every distinct string cached a handle, a thread and two fds.

    The guard is the one every `/api/*` route already used, so a name that would earn a 400 there
    draws the no-repo page here rather than booting live against a repo the poll then refuses.
    """
    readers = _OneRepo()
    html = repo_page(readers)(repo)
    assert readers.asked == []
    assert _globals(html)["__AUDITOR_OBSERVER__"]["repo"] == ""


def test_the_status_badge_reads_a_daemon_word_and_not_a_loop_state(daemon_router):
    """Spec 8.3's `LoopState` is per repo; `repos[i].state` is the badge, this is the daemon."""
    assert daemon_router.state == "running"
    daemon_router.restarting = True
    assert daemon_router.state == "restarting"


def test_idle_seconds_is_the_gap_before_the_request_being_served(daemon_router):
    """Measured against the previous request, never against this one, or it would always be 0."""
    daemon_router.dispatch("POST", "/sessions/heartbeat", {}, b'{"session_id": "s1"}')
    first = daemon_router.last_request
    daemon_router.last_request = first - 12.0
    daemon_router.dispatch("GET", "/health", {}, b"")
    assert daemon_router.idle_seconds == pytest.approx(12.0, abs=0.5)


def test_the_first_request_measures_idleness_from_the_daemon_s_start(daemon_router):
    """`last_request` is 0.0 until something arrives, and 1970 is not an idle window."""
    daemon_router.started_at = time.time() - 5.0
    daemon_router.dispatch("GET", "/health", {}, b"")
    assert daemon_router.idle_seconds == pytest.approx(5.0, abs=0.5)


@pytest.mark.parametrize(
    "target",
    [
        "/",
        "/health",
        "/api/status",
        "/api/runs",
        "/api/refinements",
        "/api/evals",
        "/api/flow",
        "/api/runs/r-1",
    ],
)
def test_no_read_is_what_the_idle_timer_counts(daemon_router, tmp_path, target: str):
    """Spec 8.1: reading the page must not decide how long the process lives (P21).

    Every path here is one the page fetches, on a timer or on a panel opening, and a list of the
    two the first page happened to poll left the other four holding the daemon open.
    """
    daemon_router.dispatch("POST", "/sessions/heartbeat", {}, b'{"session_id": "s1"}')
    real = daemon_router.last_request
    daemon_router.dispatch("GET", f"{target}?repo={tmp_path}", {}, b"")
    assert daemon_router.last_request == real


def test_a_page_polling_past_the_idle_window_still_lets_the_daemon_exit(daemon_router):
    """`daemon.py:743-744` feeds `last_request` to the timer; this is that line, on a clock."""
    idle = IdleTimer(minutes=1.0, now=0.0)
    for _ in range(40):  # 40 polls is two idle windows at 3 s a poll
        daemon_router.dispatch("GET", "/api/status", {}, b"")
        if daemon_router.last_request > idle.last:
            idle.touch(daemon_router.last_request)
    assert idle.due(120.0) is True


def test_a_write_is_the_activity_that_holds_the_daemon_open(daemon_router):
    """The other half: a hook spooling an edit or attaching a session must push the deadline."""
    idle = IdleTimer(minutes=1.0, now=0.0)
    daemon_router.started_at = 0.0
    daemon_router.last_request = 0.0
    daemon_router.dispatch("POST", "/sessions/heartbeat", {}, b'{"session_id": "s1"}')
    idle.touch(daemon_router.last_request)
    assert idle.due(daemon_router.last_request + 30.0) is False


def test_a_runner_with_no_model_of_its_own_is_a_row_with_no_model():
    """`codex_model` is empty by default, and the fallback would name Claude's beside Codex."""
    roster = Readers(settings=UserSettings()).roster()
    by_runner = {row.runner: row.model for row in roster}
    assert by_runner[RunnerKind.CLAUDE]
    assert by_runner[RunnerKind.CODEX] == ""


def test_the_status_poll_carries_a_runner_roster_so_the_eval_block_can_say_no_eval_yet():
    """`/api/evals` returned nothing at all, so the page could not tell "none" from "not run"."""
    roster = Readers(settings=UserSettings()).roster()
    assert [row.runner for row in roster] == list(MODEL_RUNNERS)
    assert all(
        row.measured == 0 and row.proven == 0 and row.strata == () for row in roster
    )


def test_the_roster_leaves_out_the_test_double_and_the_assessment_sentinel():
    """`RunnerKind` has four members and only two of them ever answer for a model."""
    assert set(MODEL_RUNNERS) == {RunnerKind.CLAUDE, RunnerKind.CODEX}
    assert set(RunnerKind) - set(MODEL_RUNNERS) == {RunnerKind.FAKE, RunnerKind.NONE}
