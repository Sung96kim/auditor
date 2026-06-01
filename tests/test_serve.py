"""serve.py — the in-memory localhost report server."""

import threading
from urllib.request import urlopen

from auditor.serve import ReportServer


def test_serve_returns_document_on_localhost():
    server = ReportServer("<h1>hello</h1>")
    assert server.url.startswith("http://127.0.0.1:")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(server.url) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "text/html; charset=utf-8"
            assert resp.read().decode() == "<h1>hello</h1>"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_serve_binds_loopback_only():
    # never exposes the report beyond the local machine (reports may carry source).
    server = ReportServer("x")
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
