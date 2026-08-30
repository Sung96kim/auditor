"""The daemon's transport and its routes, over a real loopback port."""

import asyncio
import http.client
import json
import threading
import time

from auditor.graph.refine.lock import rebuild_lock
from auditor.observer.events import MAX_EVENT_PATHS
from auditor.observer.payloads import ROUTES, StatusPayload
from auditor.observer.routes import HANDLERS, TAGS
from auditor.observer.server import MAX_BODY_BYTES

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


def test_events_answers_202_while_a_rebuild_holds_the_identity_lock(
    daemon_server, tmp_path, monkeypatch
):
    """Spec 20's spike: `POST /events` takes no lock, so a rebuild cannot stall a hook."""
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
            {"repo": str(tmp_path / "src"), "key": "k", "paths": ["a.py"]},
        )
        elapsed = time.monotonic() - started
        assert not release.is_set()  # the rebuild is still holding the lock
    finally:
        release.set()
        worker.join(timeout=10.0)
    assert status == 202
    assert body == {"accepted": 1, "dropped": 0, "queued_repos": 1}
    assert elapsed < 1.0


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
            "key": "k",
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


def test_a_conditional_get_answers_304_without_running_the_query(
    daemon_server, readers
):
    """The tag exists to avoid `LogQuery.page`, so a 304 must not pay for one (P14)."""
    _, call = daemon_server
    first, headers, _ = call.request("GET", "/api/runs?repo=/r")
    assert first == 200
    assert readers.page_calls == 1
    again, _, _ = call.request(
        "GET", "/api/runs?repo=/r", headers={"If-None-Match": headers["ETag"]}
    )
    assert again == 304
    assert readers.page_calls == 1


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


def test_the_status_body_names_only_what_this_slice_fills(daemon_server):
    """S10 is written against these shapes, so a default must be distinguishable from a value."""
    _, call = daemon_server
    _, _, body = call.request("GET", "/api/status")
    defaults = json.loads(
        StatusPayload(home="", version="", compat=0).model_dump_json()
    )
    moved = {key for key, value in body.items() if value != defaults[key]}
    assert moved <= _FILLED
    assert body["queued_repos"] == 0
    assert body["sessions"] == []
    assert body["budget"] is None


def test_a_reader_that_raises_is_a_json_500_not_a_dropped_connection(
    daemon_server, readers
):
    """`socketserver` closes the connection with no answer, and the traceback goes nowhere (P30)."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("BOOM")

    readers.runs_tag = boom
    _, call = daemon_server
    status, _, body = call.request("GET", "/api/runs?repo=/r")
    assert status == 500
    assert "GET /api/runs" in body["error"]


def test_an_over_long_body_is_refused_rather_than_truncated(daemon_server):
    """A truncated read leaves the rest of the body to be parsed as the next request line."""
    server, _ = daemon_server
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
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
            "key": "k",
            "paths": [f"f{i}.py" for i in range(MAX_EVENT_PATHS + 1)],
        },
    )
    assert status == 400
    assert "unusable event body" in body["error"]


def test_an_unknown_run_id_is_a_json_404(daemon_server):
    """`/api/runs/` with no id and an id the ledger never held answer the same way."""
    _, call = daemon_server
    status, _, body = call.request("GET", "/api/runs/nope")
    assert status == 404
    assert "nope" in body["error"]
    assert call.request("GET", "/api/runs/")[0] == 404


def test_a_detach_bumps_the_revision_so_the_badge_moves(daemon_server, daemon_router):
    """P14 says the counter moves on attach, detach and restart; detach was the unpinned one."""
    _, call = daemon_server
    before = daemon_router.revision
    call.request("POST", "/sessions/detach", {"session_id": "nobody"})
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
    daemon_router.gate = lambda request: "the repo is not configured for auditor"
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
    _, _, body = call.request("POST", "/admin/restart")
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
