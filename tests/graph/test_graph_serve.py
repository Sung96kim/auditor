import json

from auditor.graph.viz import build_payload, render_app


async def test_render_app_injects_payload(viz_store):
    payload = await build_payload(viz_store)
    html = render_app(payload)
    assert "__AUDITOR_GRAPH__" in html
    assert '"m.py::Foo"' in html  # the data is embedded
    assert html.strip().lower().startswith("<!doctype html") or "<html" in html.lower()
    # the injected JSON round-trips
    assert html.index("__AUDITOR_GRAPH__") > 0
    assert json.dumps(payload["meta"]["accent"]) in html
