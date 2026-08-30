"""The daemon's transport and its routes, over a real loopback port."""

import asyncio
import http.client
import json
import socket
import threading
import time

import pytest

from auditor.graph.payloads import RunRowPayload
from auditor.graph.refine.lock import rebuild_lock
from auditor.graph.refine.models import (
    ClientKind,
    ProducerKind,
    RunnerKind,
    RunStatus,
    TriggerKind,
)
from auditor.observer.events import MAX_EVENT_PATHS
from auditor.observer.payloads import (
    ROUTES,
    BudgetPayload,
    RateLimitPayload,
    RepoPayload,
    ReposPayload,
    RunDetailView,
    StatusPayload,
)
from auditor.observer.routes import HANDLERS, TAGS
from auditor.observer.server import (
    MAX_BODY_BYTES,
    REQUEST_TIMEOUT,
    _Handler,
    loopback_host,
)

#: one legal `repo_dir_key`: `EventRequest.key` names a directory, so its shape is constrained
_KEY = "a" * 40

#: what S8b actually fills on `/api/status`; everything else stays at its default until S8c
_FILLED = {
    "home",
    "version",
    "compat",
    "started_at",
    "uptime_seconds",
    "queued_repos",
    "sessions",
}


def _raw(port: int, request: str, *, timeout: float = 5.0) -> str:
    """One hand-written request over a raw socket, for headers `http.client` will not send.

    Reads the headers, then exactly the body they declare, so a keep-alive answer does not
    block until the deadline.
    """
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(request.encode())
        seen = b""
        while b"\r\n\r\n" not in seen:
            got = sock.recv(4096)
            if not got:
                return seen.decode(errors="replace")
            seen += got
        head, _, body = seen.partition(b"\r\n\r\n")
        declared = (
            0
            if request.startswith("HEAD ")
            else int(
                next(
                    (
                        line.split(b":", 1)[1]
                        for line in head.split(b"\r\n")
                        if line.lower().startswith(b"content-length:")
                    ),
                    b"0",
                )
            )
        )
        while len(body) < declared:
            got = sock.recv(4096)
            if not got:
                break
            body += got
    return (head + b"\r\n\r\n" + body).decode(errors="replace")


def test_every_route_in_the_table_has_exactly_one_handler():
    """A stringly-keyed `getattr` would erase this pairing, so the table is explicit."""
    assert set(HANDLERS) == set(ROUTES)


def test_every_conditional_route_has_exactly_one_tag_function():
    """The tag is computed before the handler, so a polled route with no tag would 200 forever."""
    assert set(TAGS) == {route for route, spec in ROUTES.items() if spec.etag}


def test_the_server_binds_loopback_only(daemon_server):
    """An audit graph and a verbatim prompt never leave this machine (spec 12.1)."""
    server, _ = daemon_server
    assert server.server_address[0] == "127.0.0.1"
    assert 0 < server.port < 65536


def test_health_answers_the_four_fields_ensure_compares(daemon_server, tmp_path):
    _, call = daemon_server
    status, _, body = call.request("GET", "/health")
    assert status == 200
    assert body == {
        "home": str(tmp_path / "home"),
        "db_path": str(tmp_path / "home" / "index.db"),
        "version": "0.10.5",
        "compat": 1,
    }


def test_an_unknown_route_is_a_json_404_not_a_traceback(daemon_server):
    _, call = daemon_server
    status, _, body = call.request("GET", "/api/nope")
    assert status == 404
    assert body == {"error": "no route for GET /api/nope"}


def test_the_page_is_served_outside_the_api_table(daemon_server):
    """`ROUTES` is spec 12.1's API line; the page is HTML and names no payload model (P13)."""
    _, call = daemon_server
    status, headers, body = call.request("GET", "/")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "observer" in body


def test_events_spools_and_answers_202_without_waiting_on_the_rebuild_lock(
    daemon_server, daemon_router, tmp_path, monkeypatch
):
    """Spec 20's spike: `POST /events` takes no lock, so a rebuild cannot stall a hook.

    The discriminators are the elapsed time and the spool landing on disk while the rebuild
    still holds the lock; `Router.events` consults no lock on any path, by design.
    """
    monkeypatch.setenv("AUDITOR_HOME", str(tmp_path / "home"))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    _, call = daemon_server
    held, release = threading.Event(), threading.Event()

    async def hold() -> None:
        async with rebuild_lock("identity"):
            held.set()
            await asyncio.to_thread(release.wait, 10.0)

    worker = threading.Thread(target=lambda: asyncio.run(hold()), daemon=True)
    worker.start()
    assert held.wait(5.0)
    started = time.monotonic()
    try:
        status, _, body = call.request(
            "POST",
            "/events",
            {"repo": str(tmp_path / "src"), "key": _KEY, "paths": ["a.py"]},
        )
        elapsed = time.monotonic() - started
        assert not release.is_set()  # the rebuild is still holding the lock
    finally:
        release.set()
        worker.join(timeout=10.0)
    assert status == 202
    assert body == {"accepted": 1, "dropped": 0, "queued_repos": 1}
    assert elapsed < 1.0
    assert daemon_router.deps.queue.keys() == (_KEY,)
    assert (tmp_path / "repos" / _KEY / "spool.jsonl").exists()


def test_events_mirrors_stage_0_and_still_admits_a_deleted_path(
    daemon_server, tmp_path
):
    """`auditable_shape` is the shape alone, so a `D` entry reaches stage 1 (S8a P13)."""
    (tmp_path / "src").mkdir()
    _, call = daemon_server
    status, _, body = call.request(
        "POST",
        "/events",
        {
            "repo": str(tmp_path / "src"),
            "key": _KEY,
            "paths": ["gone.py", "notes.md", "node_modules/x/a.py"],
        },
    )
    assert status == 202
    assert body == {"accepted": 1, "dropped": 2, "queued_repos": 1}


def test_the_runs_etag_is_stable_and_a_repeat_poll_gets_304(daemon_server, tmp_path):
    """Spec 12.1 polls this at 3 s; without a 304 every poll re-serializes the whole page."""
    _, call = daemon_server
    first, headers, _ = call.request("GET", f"/api/runs?repo={tmp_path}")
    assert first == 200
    tag = headers["ETag"]
    again, _, _ = call.request(
        "GET", f"/api/runs?repo={tmp_path}", headers={"If-None-Match": tag}
    )
    assert again == 304


def test_a_conditional_get_answers_304_without_running_the_page_query(
    daemon_server, readers, tmp_path
):
    """The tag exists to avoid `LogQuery.page`, so a 304 must not pay for one (P14).

    The tag's own two reads are paid on every poll; what P14 buys is skipping the page query,
    which is the larger of the two.
    """
    _, call = daemon_server
    first, headers, _ = call.request("GET", f"/api/runs?repo={tmp_path}")
    assert first == 200
    assert (readers.page_calls, readers.tag_calls) == (1, 1)
    again, _, _ = call.request(
        "GET",
        f"/api/runs?repo={tmp_path}",
        headers={"If-None-Match": headers["ETag"]},
    )
    assert again == 304
    assert readers.page_calls == 1  # the page query is what the 304 skips
    assert readers.tag_calls == 2  # the tag itself is still paid for, once per poll
    readers.rows += 1  # a run lands, so the tag moves and the page is served again
    moved, _, _ = call.request(
        "GET",
        f"/api/runs?repo={tmp_path}",
        headers={"If-None-Match": headers["ETag"]},
    )
    assert moved == 200
    assert readers.page_calls == 2


def test_the_status_tag_does_not_survive_a_restart(daemon_server, daemon_router):
    """A page holding `W/"0"` from a dead daemon would get a 304 from a brand new one (P14)."""
    _, call = daemon_server
    _, headers, _ = call.request("GET", "/api/status")
    tag = headers["ETag"]
    assert call.request("GET", "/api/status", headers={"If-None-Match": tag})[0] == 304
    daemon_router.started_at += 1.0
    daemon_router.revision = 0
    status, headers, _ = call.request(
        "GET", "/api/status", headers={"If-None-Match": tag}
    )
    assert status == 200
    assert headers["ETag"] != tag


def test_the_status_body_names_only_what_this_slice_fills(daemon_server, tmp_path):
    """S10 is written against these shapes, so a default must be distinguishable from a value.

    The three counters are asserted with something in them, because on an empty daemon they are
    the model's own defaults and a field that stopped being filled would read the same.
    """
    (tmp_path / "src").mkdir()
    _, call = daemon_server
    call.request("POST", "/sessions/attach", {"repo": "/r", "session_id": "s1"})
    call.request(
        "POST",
        "/events",
        {"repo": str(tmp_path / "src"), "key": _KEY, "paths": ["a.py"]},
    )
    _, _, body = call.request("GET", "/api/status")
    defaults = json.loads(
        StatusPayload(home="", version="", compat=0).model_dump_json()
    )
    moved = {key for key, value in body.items() if value != defaults[key]}
    assert moved == _FILLED
    assert body["queued_repos"] == 1
    assert len(body["sessions"]) == 1
    assert body["budget"] is None


def test_a_reader_that_raises_is_a_json_500_not_a_dropped_connection(
    daemon_server, readers, tmp_path
):
    """`socketserver` closes the connection with no answer, and the traceback goes nowhere (P30)."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("BOOM")

    readers.runs_tag = boom
    _, call = daemon_server
    status, _, body = call.request("GET", f"/api/runs?repo={tmp_path}")
    assert status == 500
    assert "GET /api/runs" in body["error"]


def test_an_over_long_body_is_refused_rather_than_truncated(daemon_server):
    """A truncated read leaves the rest of the body to be parsed as the next request line."""
    server, _ = daemon_server
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=1)
    conn.putrequest("POST", "/events")
    conn.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
    conn.endheaders()  # the declared length is refused before a byte of body is read
    response = conn.getresponse()
    body = json.loads(response.read())
    assert response.status == 413
    assert "over" in body["error"]
    assert response.getheader("Connection") == "close"
    conn.close()


def test_a_path_set_over_the_cap_is_a_400(daemon_server, tmp_path):
    """`auditable_shape` runs once per path in the request thread, so the list needs a ceiling."""
    _, call = daemon_server
    status, _, body = call.request(
        "POST",
        "/events",
        {
            "repo": str(tmp_path),
            "key": _KEY,
            "paths": [f"f{i}.py" for i in range(MAX_EVENT_PATHS + 1)],
        },
    )
    assert status == 400
    assert "unusable event body" in body["error"]


def test_an_unknown_run_id_is_a_json_404(daemon_server, tmp_path):
    """The no-route 404 also carries the id, so only the exact body pins the route itself."""
    _, call = daemon_server
    status, _, body = call.request("GET", f"/api/runs/nope?repo={tmp_path}")
    assert status == 404
    assert body == {"error": "no run nope in this repo's ledger"}
    assert call.request("GET", f"/api/runs/?repo={tmp_path}")[0] == 404


def test_a_known_run_id_answers_the_run_detail(daemon_server, readers, tmp_path):
    """No test got a 200 here, so the route could be deleted with the whole suite green."""
    readers.detail = RunDetailView(
        repo=str(tmp_path),
        identity="id",
        run=RunRowPayload(
            run_id="r-1",
            status=RunStatus.SUCCEEDED,
            producer=ProducerKind.OBSERVER,
            client=ClientKind.CLAUDE_CODE,
            runner=RunnerKind.CLAUDE,
            trigger_kind=TriggerKind.EDIT,
        ),
        prompt="the brief",
    )
    _, call = daemon_server
    status, _, body = call.request("GET", f"/api/runs/r-1?repo={tmp_path}")
    assert status == 200
    assert body["run"]["run_id"] == "r-1"
    assert body["prompt"] == "the brief"


def test_a_detach_bumps_the_revision_only_for_a_session_the_daemon_held(
    daemon_server, daemon_router
):
    """P14 says the counter moves on a session leaving; a hook spamming detach must not move it."""
    _, call = daemon_server
    call.request("POST", "/sessions/attach", {"repo": "/r", "session_id": "s1"})
    before = daemon_router.revision
    call.request("POST", "/sessions/detach", {"session_id": "nobody"})
    assert daemon_router.revision == before
    call.request("POST", "/sessions/detach", {"session_id": "s1"})
    assert daemon_router.revision == before + 1


def test_the_status_etag_moves_when_the_daemon_changes_state(
    daemon_server, daemon_router
):
    """`/api/status` has no table behind it, so its tag is the daemon's own revision (recon Q4)."""
    _, call = daemon_server
    _, headers, _ = call.request("GET", "/api/status")
    tag = headers["ETag"]
    assert call.request("GET", "/api/status", headers={"If-None-Match": tag})[0] == 304
    daemon_router.bump()
    status, headers, _ = call.request(
        "GET", "/api/status", headers={"If-None-Match": tag}
    )
    assert status == 200
    assert headers["ETag"] != tag


def test_attach_answers_the_three_fields_and_names_a_refusal(
    daemon_server, daemon_router
):
    """Spec 8.2's `{attached, reason, page_url}`; a refusal carries the clause that refused."""
    _, call = daemon_server
    _, _, body = call.request(
        "POST", "/sessions/attach", {"repo": "/r", "session_id": "s1"}
    )
    assert body == {"attached": True, "reason": "", "page_url": daemon_router.url}
    daemon_router.deps = daemon_router.deps.model_copy(
        update={"gate": lambda request: "the repo is not configured for auditor"}
    )
    _, _, refused = call.request(
        "POST", "/sessions/attach", {"repo": "/r", "session_id": "s2"}
    )
    assert refused == {
        "attached": False,
        "reason": "the repo is not configured for auditor",
        "page_url": "",
    }


def test_a_heartbeat_for_an_unknown_session_is_answered_not_raised(daemon_server):
    _, call = daemon_server
    status, _, body = call.request(
        "POST", "/sessions/heartbeat", {"session_id": "never"}
    )
    assert status == 200
    assert body == {"ok": False, "reason": "no such session"}


def test_a_restart_refuses_the_next_attach_so_ensure_returns_at_once(daemon_server):
    """Spec 8.1: `ensure` returns immediately and the next `ensure` attaches (recon Q8)."""
    _, call = daemon_server
    _, _, body = call.request("POST", "/admin/restart", {"compat": 99})
    assert body == {"restarting": True, "reason": "wire compat mismatch"}
    _, _, attach = call.request(
        "POST", "/sessions/attach", {"repo": "/r", "session_id": "s1"}
    )
    assert attach["attached"] is False
    assert attach["reason"] == "the daemon is restarting"


def test_the_page_is_opened_once_per_daemon_lifetime(
    daemon_server, daemon_router, opened
):
    """Spec 12.1: on first attach only, so a second session does not raise a second window."""
    _, call = daemon_server
    call.request("POST", "/sessions/attach", {"repo": "/r", "session_id": "s1"})
    call.request("POST", "/sessions/attach", {"repo": "/r", "session_id": "s2"})
    assert opened == [daemon_router.url]


def test_an_unusable_event_body_is_a_400_not_a_500(daemon_server):
    """A hook that posted the wrong shape gets an answer it can log, not a broken connection."""
    _, call = daemon_server
    status, _, body = call.request("POST", "/events", {"paths": ["a.py"]})
    assert status == 400
    assert "unusable event body" in body["error"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.1:7682", True),
        ("localhost:7682", True),
        ("[::1]:7682", True),
        ("", True),
        (None, True),
        ("evil.example", False),
        ("evil.example:7682", False),
        ("10.0.0.5:7682", False),
    ],
)
def test_only_a_loopback_host_passes_the_header_check(raw, expected):
    """DNS rebinding answers on a name that resolves here, so the name is what has to be read."""
    assert loopback_host(raw) is expected


def test_a_request_carrying_an_origin_is_refused_before_any_side_effect(
    daemon_server, daemon_router
):
    """A `text/plain` POST is a CORS simple request, so any page could otherwise restart us."""
    _, call = daemon_server
    status, _, body = call.request(
        "POST", "/admin/restart", {}, {"Origin": "https://evil.example"}
    )
    assert status == 403
    assert body == {"error": "cross-origin requests are refused"}
    assert daemon_router.restarting is False


def test_a_non_loopback_host_is_refused(daemon_server):
    """Binding to 127.0.0.1 stops other hosts; only the Host header stops a rebinding page."""
    server, _ = daemon_server
    answer = _raw(server.port, "GET /api/status HTTP/1.1\r\nHost: evil.example\r\n\r\n")
    assert "403" in answer.splitlines()[0]
    assert "only a loopback Host is answered" in answer


def test_the_same_origin_page_still_loads_under_both_checks(daemon_server):
    """The bundle at `/` makes no cross-origin request, so neither check may reach it (E2)."""
    server, call = daemon_server
    status, headers, body = call.request("GET", "/")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "observer" in body
    answer = _raw(
        server.port, f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n\r\n"
    )
    assert "200" in answer.splitlines()[0]


@pytest.mark.parametrize(
    ("declared", "status", "reason"),
    [
        ("abc", 400, "unreadable Content-Length"),
        ("-1", 400, "negative Content-Length"),
    ],
)
def test_an_unusable_content_length_is_answered_not_dropped(
    daemon_server, declared, status, reason
):
    """Parsed outside the handler's `try`, `abc` dropped the connection and `-1` read to EOF."""
    server, _ = daemon_server
    answer = _raw(
        server.port,
        f"POST /events HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        f"Content-Length: {declared}\r\n\r\n",
    )
    assert str(status) in answer.splitlines()[0]
    assert reason in answer


def test_a_short_body_frees_its_thread_on_the_deadline(daemon_server, monkeypatch):
    """No read deadline meant N half-open connections cost N pinned threads forever.

    The deadline is asserted before it is shortened: `StreamRequestHandler` declares its own
    `timeout = None`, so the monkeypatch alone passes whether or not production sets one.
    """
    assert _Handler.timeout == REQUEST_TIMEOUT
    monkeypatch.setattr(_Handler, "timeout", 0.3)

    server, _ = daemon_server
    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", server.port), timeout=5.0) as sock:
        sock.sendall(
            b"POST /events HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Content-Length: 100\r\n\r\nshort"
        )
        assert (
            sock.recv(4096) == b""
        )  # the daemon gave up and closed, rather than waiting
    assert time.monotonic() - started < 4.0


def test_a_chunked_body_is_refused_rather_than_desyncing_the_connection(daemon_server):
    """With no Content-Length the chunks stay in the socket and parse as the next request."""
    server, _ = daemon_server
    answer = _raw(
        server.port,
        "POST /events HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        "Transfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n",
    )
    assert "411" in answer.splitlines()[0]
    assert "a Content-Length is required" in answer


@pytest.mark.parametrize(
    ("method", "path"),
    [("HEAD", "/api/nope"), ("PUT", "/"), ("DELETE", "/"), ("PUT", "/api/nope")],
)
def test_an_unhandled_method_or_path_is_a_json_404_not_stdlib_html(
    daemon_server, method, path
):
    """A stdlib handler answers 501 with an HTML banner; every miss here is JSON instead.

    `PUT /` is in the list because the page is answered before the table: only the two read
    methods reach it, and a write to `/` has to fall through to the table's own 404.
    """
    server, _ = daemon_server
    answer = _raw(server.port, f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    assert "404" in answer.splitlines()[0]
    assert "application/json" in answer
    assert ("no route for" in answer) is (method != "HEAD")


def test_head_on_the_page_answers_the_headers_a_get_would(daemon_server):
    """A HEAD is a GET minus the body, so 404ing it hid the page from every proxy and probe."""
    _, call = daemon_server
    _, headers, body = call.request("GET", "/")
    status, head_headers, head_body = call.request("HEAD", "/")
    assert (status, head_body) == (200, "")
    assert head_headers["Content-Type"] == headers["Content-Type"]
    assert head_headers["Content-Length"] == str(len(body.encode()))


def test_a_304_carries_no_content_length_of_its_own(daemon_server):
    """`Content-Length: 0` on a 304 describes the cached body, which is not zero bytes long."""
    server, call = daemon_server
    tag = call.request("GET", "/api/status")[1]["ETag"]
    answer = _raw(
        server.port,
        f"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\nIf-None-Match: {tag}\r\n\r\n",
    )
    assert "304" in answer.splitlines()[0]
    assert "content-length" not in answer.lower()


def test_a_head_carries_no_body_so_the_connection_stays_in_step(daemon_server):
    """A body on a HEAD answer is read as the next request line, and every later answer slides."""
    server, _ = daemon_server
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=2)
    try:
        conn.request("HEAD", "/")
        head = conn.getresponse()
        assert head.status == 200
        assert (
            int(head.headers["Content-Length"]) > 0
        )  # the page's length, with no page
        assert head.read() == b""

        conn.request("GET", "/health")
        answer = conn.getresponse()
        assert answer.status == 200
        assert json.loads(answer.read())["compat"] == 1
    finally:
        conn.close()


def test_a_spool_key_cannot_escape_the_home(daemon_server, tmp_path):
    """`key` reached `spool_path` verbatim, so a traversal wrote a spool wherever it named."""
    _, call = daemon_server
    (tmp_path / "src").mkdir()
    status, _, body = call.request(
        "POST",
        "/events",
        {"repo": str(tmp_path / "src"), "key": "../PWNED", "paths": ["a.py"]},
    )
    assert status == 400
    assert "unusable event body" in body["error"]
    assert not (tmp_path / "PWNED").exists()


@pytest.mark.parametrize(
    "route", ["/api/repos", "/api/graph", "/api/refinements", "/api/evals", "/api/flow"]
)
def test_every_read_route_answers_the_payload_its_route_spec_names(
    daemon_server, tmp_path, route
):
    """Four handlers could be repointed at `api_repos` with the whole suite green (H7)."""
    _, call = daemon_server
    status, _, body = call.request("GET", f"{route}?repo={tmp_path}&symbol=x")
    assert status == 200
    payload = ROUTES[("GET", route)].payload
    assert json.loads(payload.model_validate(body).model_dump_json()) == body


@pytest.mark.parametrize(
    "query", ["", "?repo=", "?repo=/nope-xyz", "?repo=.", "?repo=..", "?repo=auditor"]
)
@pytest.mark.parametrize(
    "route",
    [
        "/api/graph",
        "/api/runs",
        "/api/runs/r-1",
        "/api/refinements",
        "/api/evals",
        "/api/flow",
    ],
)
def test_a_repo_scoped_route_refuses_to_answer_from_the_daemons_cwd(
    daemon_server, route, query
):
    """`Path(query.get("repo") or ".")` answered every one of these from the daemon's own cwd.

    A relative name is that same cwd wearing a query string: `.`, `..` and `auditor` are all
    directories the daemon can see and none of them is a repo the caller named.
    """
    _, call = daemon_server
    status, _, body = call.request("GET", f"{route}{query}")
    assert status == 400
    assert body == {"error": "a repo=<absolute path> naming a directory is required"}


def test_an_event_bumps_the_revision_so_the_queue_count_is_pollable(
    daemon_server, daemon_router, tmp_path
):
    """`queued_repos` is one of the seven fields this slice fills, and the tag hid every change."""
    (tmp_path / "src").mkdir()
    _, call = daemon_server
    before = daemon_router.revision
    call.request(
        "POST",
        "/events",
        {"repo": str(tmp_path / "src"), "key": _KEY, "paths": ["a.py"]},
    )
    assert daemon_router.revision == before + 1


def test_a_restart_a_compatible_caller_asks_for_is_declined(
    daemon_server, daemon_router
):
    """The route had no model behind it, so any local process could re-exec the daemon (M10)."""
    _, call = daemon_server
    status, _, body = call.request("POST", "/admin/restart", {"compat": 1})
    assert status == 200
    assert body == {"restarting": False, "reason": "the wire is already compatible"}
    assert daemon_router.restarting is False
    bad, _, refused = call.request("POST", "/admin/restart", {"compat": "not a number"})
    assert bad == 400
    assert "unusable restart body" in refused["error"]
    assert daemon_router.restarting is False


def test_a_re_attach_keeps_the_session_it_already_had(daemon_server, daemon_router):
    """A second attach for one id is the same session, so its age must not restart (L3)."""
    _, call = daemon_server
    call.request("POST", "/sessions/attach", {"repo": "/r", "session_id": "s1"})
    first = daemon_router.deps.sessions.live(now=time.time())[0]
    call.request("POST", "/sessions/attach", {"repo": "/r", "session_id": "s1"})
    again = daemon_router.deps.sessions.live(now=time.time())[0]
    assert again.started_at == first.started_at
    assert again.last_seen >= first.last_seen


def test_the_status_route_reports_the_loop_state_per_repo(
    daemon_router, daemon_server, readers
):
    """Spec 12.1's badge is per repo, because the page has a switcher (Seam 2)."""
    readers.repos = lambda: ReposPayload(
        repos=(RepoPayload(repo="/r", identity="/r/.git", repo_dir_key="k"),)
    )
    daemon_router.deps = daemon_router.deps.model_copy(
        update={"loop_state": lambda key: "observing"}
    )
    _server, caller = daemon_server
    status, _headers, body = caller.request("GET", "/api/status")
    assert status == 200
    assert body["repos"]
    assert all(repo["state"] == "observing" for repo in body["repos"])


def test_the_status_route_draws_both_meters(daemon_router, daemon_server):
    """S8b declared `budget` and `limits` and left them empty; this slice fills them (Seam 3)."""
    daemon_router.deps = daemon_router.deps.model_copy(
        update={
            "meters": lambda: (
                BudgetPayload(spent_usd=0.5, max_cost_usd_per_day=2.0),
                RateLimitPayload(max_utilization=0.5, paused=True, resumes_at=500.0),
            )
        }
    )
    _server, caller = daemon_server
    _status, _headers, body = caller.request("GET", "/api/status")
    assert body["budget"]["spent_usd"] == 0.5
    assert (body["limits"]["paused"], body["limits"]["resumes_at"]) == (True, 500.0)


def test_a_loop_transition_moves_the_status_etag(daemon_router, daemon_server):
    """P14 of S8b: the counter is the tag, and S8c is what moves it most."""
    _server, caller = daemon_server
    _status, headers, _body = caller.request("GET", "/api/status")
    tag = headers["ETag"]
    daemon_router.bump()
    _status, headers, _body = caller.request("GET", "/api/status")
    assert headers["ETag"] != tag
