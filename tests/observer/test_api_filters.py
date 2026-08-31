"""Spec 12.1's three request-side controls: collapsed `skipped` rows, direction and depth."""

import time

import pytest

from auditor.graph.flow import FlowDirection
from auditor.graph.model import MAX_FLOW_DEPTH
from auditor.graph.payloads import LogFilter, LogView
from auditor.observer.routes import filter_key


def test_the_default_run_stream_still_hides_skipped_rows(
    daemon_server, readers, tmp_path
):
    """`graph log`'s own default, unchanged: an assessment-only row is not the stream."""
    _, call = daemon_server
    status, _, _ = call.request("GET", f"/api/runs?repo={tmp_path}")
    assert status == 200
    assert readers.filters[-1].skipped is False


def test_skipped_1_reaches_the_filter_so_the_panel_can_expand_a_collapsed_row(
    daemon_server, readers, tmp_path
):
    """`parse_qs` drops a bare `?skipped`, so the page sends `skipped=1` and this pins it."""
    _, call = daemon_server
    status, _, _ = call.request("GET", f"/api/runs?repo={tmp_path}&skipped=1")
    assert status == 200
    assert readers.filters[-1].skipped is True


def test_a_status_list_and_a_window_reach_the_filter(daemon_server, readers, tmp_path):
    """One filter means one thing on both surfaces: the route parses with `LogFilter.of`."""
    _, call = daemon_server
    call.request(
        "GET", f"/api/runs?repo={tmp_path}&status=failed,succeeded&since=2h&limit=7"
    )
    chosen = readers.filters[-1]
    assert chosen.statuses == ("failed", "succeeded")
    assert chosen.limit == 7
    assert chosen.since == pytest.approx(time.time() - 7200, abs=60)


@pytest.mark.parametrize(
    ("query", "names"),
    [
        ("status=sideways", "status"),
        ("since=whenever", "since"),
        ("limit=abc", "limit"),
        ("status=", "status"),
        ("since=", "since"),
        ("limit=", "limit"),
        ("skipped=", "skipped"),
    ],
    ids=[
        "status",
        "since",
        "limit",
        "empty status",
        "empty since",
        "empty limit",
        "empty skipped",
    ],
)
def test_an_unusable_filter_is_a_400_naming_the_field(
    daemon_server, tmp_path, query, names
):
    """A 500 from a query string is the failure this replaces; the CLI's own message is reused.

    The four empty cases are the asymmetry: `since=` refused and the other three defaulted, so
    one typo was a 400 and three were silently the value the caller did not ask for.
    """
    _, call = daemon_server
    status, _, body = call.request("GET", f"/api/runs?repo={tmp_path}&{query}")
    assert status == 400
    assert names in body["error"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("skipped=1", True),
        ("skipped=true", True),
        ("skipped=yes", True),
        ("skipped=on", True),
        ("skipped=0", False),
        ("skipped=false", False),
        ("skipped=off", False),
        ("skipped=no", False),
    ],
)
def test_the_skipped_flag_reads_both_ways(
    daemon_server, readers, tmp_path, query, expected
):
    """Only the `1` direction was pinned, so `_flag` reduced to `raw is not None` stayed green."""
    _, call = daemon_server
    status, _, _ = call.request("GET", f"/api/runs?repo={tmp_path}&{query}")
    assert status == 200
    assert readers.filters[-1].skipped is expected


def test_the_view_is_never_taken_from_the_query(daemon_server, readers, tmp_path):
    """One route, one view: `/api/runs` answers runs however the caller spells the query (P16)."""
    _, call = daemon_server
    status, _, _ = call.request("GET", f"/api/runs?repo={tmp_path}&view=refinements")
    assert status == 200
    assert readers.filters[-1].view is LogView.RUNS


def test_two_windows_over_one_ledger_do_not_share_an_etag(daemon_server, tmp_path):
    """Without this a page that asked for skipped rows 304s onto the rows it was already shown."""
    _, call = daemon_server
    _, plain, _ = call.request("GET", f"/api/runs?repo={tmp_path}")
    _, with_skipped, _ = call.request("GET", f"/api/runs?repo={tmp_path}&skipped=1")
    assert plain["ETag"] and with_skipped["ETag"]
    assert plain["ETag"] != with_skipped["ETag"]


def test_one_window_still_304s_on_its_own_tag(daemon_server, tmp_path):
    """The filter is in the tag, so the conditional GET the page relies on has to still work."""
    _, call = daemon_server
    _, headers, _ = call.request("GET", f"/api/runs?repo={tmp_path}&skipped=1")
    status, _, _ = call.request(
        "GET",
        f"/api/runs?repo={tmp_path}&skipped=1",
        headers={"If-None-Match": headers["ETag"]},
    )
    assert status == 304


def test_a_window_still_304s_on_its_own_tag(daemon_server, tmp_path):
    """`parse_since` resolves against the clock, so a resolved fingerprint never repeats."""
    _, call = daemon_server
    _, headers, _ = call.request("GET", f"/api/runs?repo={tmp_path}&since=2h")
    status, _, _ = call.request(
        "GET",
        f"/api/runs?repo={tmp_path}&since=2h",
        headers={"If-None-Match": headers["ETag"]},
    )
    assert status == 304


def test_two_windows_over_one_ledger_do_not_share_an_etag_either(
    daemon_server, tmp_path
):
    """The other half of P23: `since` still discriminates, it is just fingerprinted raw."""
    _, call = daemon_server
    _, two_hours, _ = call.request("GET", f"/api/runs?repo={tmp_path}&since=2h")
    _, seven_days, _ = call.request("GET", f"/api/runs?repo={tmp_path}&since=7d")
    assert two_hours["ETag"] != seven_days["ETag"]


def test_a_filter_the_handler_will_refuse_is_never_short_circuited_to_a_304(
    daemon_server, tmp_path
):
    """`dispatch` answers 304 before the handler runs, so a bad query must produce no tag."""
    _, call = daemon_server
    status, headers, body = call.request(
        "GET",
        f"/api/runs?repo={tmp_path}&limit=abc",
        headers={"If-None-Match": 'W/"anything"'},
    )
    assert status == 400
    assert "ETag" not in headers
    assert "limit" in body["error"]


def test_the_filter_key_is_the_filter_and_nothing_else():
    """Two filters that differ anywhere must not fingerprint the same, or the ETag lies."""
    assert filter_key(LogFilter()) == filter_key(LogFilter())
    assert filter_key(LogFilter()) != filter_key(LogFilter(skipped=True))
    assert filter_key(LogFilter(limit=7)) != filter_key(LogFilter(limit=8))


def test_the_filter_key_reads_the_window_the_caller_asked_for_not_the_float():
    """A resolved `since` moves every request, so hashing it makes the tag unusable (P23)."""
    early = LogFilter.of(
        view=LogView.RUNS, status=None, since="2h", skipped=False, limit=200
    )
    later = LogFilter.of(
        view=LogView.RUNS, status=None, since="2h", skipped=False, limit=200
    )
    assert early.since != later.since
    assert filter_key(early, since="2h") == filter_key(later, since="2h")
    assert filter_key(early, since="2h") != filter_key(early, since="7d")
    assert filter_key(LogFilter(), since=None) == filter_key(LogFilter())


def test_the_direction_toggle_and_the_depth_slider_reach_the_walk(
    daemon_server, readers, tmp_path
):
    """Spec 12.1's two flow controls; `/api/flow` took only `symbol` before this."""
    _, call = daemon_server
    status, _, _ = call.request(
        "GET",
        f"/api/flow?repo={tmp_path}&symbol=build_payload&direction=in&depth=2&limit=5",
    )
    assert status == 200
    options = readers.options[-1]
    assert options is not None
    assert options.direction is FlowDirection.IN
    assert options.depth == 2 and options.limit == 5


def test_flow_with_no_controls_walks_the_shipped_defaults(
    daemon_server, readers, tmp_path
):
    _, call = daemon_server
    call.request("GET", f"/api/flow?repo={tmp_path}&symbol=build_payload")
    options = readers.options[-1]
    assert options is not None
    assert options.direction is FlowDirection.OUT
    assert options.depth == 4 and options.limit == 200


def test_an_out_of_range_depth_is_clamped_rather_than_refused(
    daemon_server, readers, tmp_path
):
    """`FlowOptions.of` clamps by design, so a slider that overshoots still walks a bounded tree."""
    _, call = daemon_server
    status, _, _ = call.request(
        "GET", f"/api/flow?repo={tmp_path}&symbol=build_payload&depth=9999"
    )
    assert status == 200
    assert readers.options[-1].depth == MAX_FLOW_DEPTH


@pytest.mark.parametrize(
    ("query", "names"),
    [
        ("direction=sideways", "direction"),
        ("depth=deep", "depth"),
        ("limit=abc", "limit"),
        ("direction=", "direction"),
        ("depth=", "depth"),
        ("limit=", "limit"),
    ],
    ids=[
        "direction",
        "depth",
        "limit",
        "empty direction",
        "empty depth",
        "empty limit",
    ],
)
def test_an_unusable_flow_control_is_a_400_naming_the_field(
    daemon_server, tmp_path, query, names
):
    _, call = daemon_server
    status, _, body = call.request(
        "GET", f"/api/flow?repo={tmp_path}&symbol=build_payload&{query}"
    )
    assert status == 400
    assert names in body["error"]
