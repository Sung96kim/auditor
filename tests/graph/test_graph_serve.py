import json
from pathlib import Path

import pytest

from auditor.graph.viz import build_payload, render_app, render_app_or_status


async def test_render_app_injects_payload(viz_store):
    payload = await build_payload(viz_store)
    html = render_app(payload)
    assert "__AUDITOR_GRAPH__" in html
    assert '"m.py::Foo"' in html  # the data is embedded
    assert html.strip().lower().startswith("<!doctype html") or "<html" in html.lower()
    # the injected JSON round-trips
    assert html.index("__AUDITOR_GRAPH__") > 0
    assert json.dumps(payload["meta"]["accent"]) in html


async def test_the_daemon_degrades_when_no_ui_bundle_was_built(viz_store, monkeypatch):
    """Spec 8.1: a missing bundle is a plain status document, never a crash."""
    monkeypatch.setattr("auditor.graph.viz._APP_HTML", Path("/nonexistent/index.html"))
    payload = await build_payload(viz_store)
    document = render_app_or_status(payload)
    assert "<html" in document.lower()
    assert "pnpm build" in document
    assert str(len(payload["nodes"])) in document


async def test_graph_serve_still_refuses_to_serve_a_page_it_cannot_build(
    viz_store, monkeypatch
):
    """`graph serve` keeps raising: its user can run `pnpm build`, and the daemon's user cannot."""
    monkeypatch.setattr("auditor.graph.viz._APP_HTML", Path("/nonexistent/index.html"))
    with pytest.raises(FileNotFoundError):
        render_app(await build_payload(viz_store))


def test_a_document_missing_its_keys_is_a_status_page_not_a_key_error(monkeypatch):
    """`GraphView.graph` defaults to `{}` and the page at `/` passes it straight through."""
    monkeypatch.setattr("auditor.graph.viz._APP_HTML", Path("/nonexistent/index.html"))
    document = render_app_or_status({})
    assert "0 nodes, 0 edges and 0 clusters" in document
